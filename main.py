#!/usr/bin/env python3
import logging
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Optional, Tuple, Dict

from aiohttp import ClientSession, FormData
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)
from openai import OpenAI

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.info("Running DreamVillaBot")

# Database configuration
DATABASE_PATH = "villa_data.db"

# Configuration management
class Config:
    def __init__(self, config_file="config.json"):
        self.config_data = self._load_config(config_file)
        
    def _load_config(self, config_file: str) -> dict:
        if not os.path.exists(config_file):
            raise OSError(f"❌ Config file not found: {config_file}")

        with open(config_file, 'r') as f:
            config = json.load(f)

        if "telegram_bot" not in config:
            logger.error(f"❌ telegram_bot not available in config file: {config}")
            raise ValueError("Missing telegram_bot configuration")

        return config["telegram_bot"]
    
    def get(self, key, default=None):
        return self.config_data.get(key, default)
    
    @property
    def bot_token(self):
        return self.config_data.get("bot_token")
    
    @property
    def api_url(self):
        return self.config_data.get("api_url")
    
    @property
    def api_methods(self):
        return self.config_data.get("api_methods", {})
    
    @property
    def messages(self):
        return self.config_data.get("messages", {})

# Database manager
class DatabaseManager:
    def __init__(self, db_path=DATABASE_PATH):
        self.db_path = db_path
        self._setup_database()
    
    def _setup_database(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS image_data (
                message_id INTEGER PRIMARY KEY,
                photo_file_id TEXT,
                legend TEXT,
                likes INTEGER DEFAULT 0
            )
            ''')
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                budget TEXT DEFAULT '300k-500k',
                location TEXT DEFAULT 'seaside',
                style TEXT DEFAULT 'modern',
                camera_angle TEXT DEFAULT 'orbit',
                current_step TEXT
            )
            ''')
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()
    
    def save_image_data(self, message_id: int, photo_file_id: str, legend: Optional[str]) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO image_data (message_id, photo_file_id, legend)
            VALUES (?, ?, ?)
            ''', (message_id, photo_file_id, legend))
            row_id = cursor.lastrowid
            conn.commit()
            return row_id
    
    def get_image_data(self, row_id: int) -> Optional[Tuple[str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT photo_file_id, legend FROM image_data WHERE rowid = ?', (row_id,))
            return cursor.fetchone()
    
    def like_image(self, row_id: int) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE image_data SET likes = likes + 1 WHERE rowid = ?', (row_id,))
            conn.commit()
            cursor.execute('SELECT likes FROM image_data WHERE rowid = ?', (row_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
    
    def get_user_preferences(self, user_id: int) -> Dict[str, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT budget, location, style, camera_angle FROM user_preferences WHERE user_id = ?', 
                (user_id,)
            )
            result = cursor.fetchone()
            
            if not result:
                # Initialize with defaults if no preferences exist
                return {
                    "budget": "300k-500k",
                    "location": "seaside",
                    "style": "modern",
                    "camera_angle": "orbit"
                }
            else:
                return {
                    "budget": result[0],
                    "location": result[1],
                    "style": result[2],
                    "camera_angle": result[3]
                }
    
    def update_user_preference(self, user_id: int, preference_type: str, value: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Try to update first
            cursor.execute(
                f'UPDATE user_preferences SET {preference_type} = ? WHERE user_id = ?', 
                (value, user_id)
            )
            # If no rows affected, do an insert
            if cursor.rowcount == 0:
                fields = ["user_id", preference_type]
                values = [user_id, value]
                placeholders = ["?"] * len(fields)
                
                query = f'''
                INSERT INTO user_preferences ({", ".join(fields)})
                VALUES ({", ".join(placeholders)})
                '''
                cursor.execute(query, values)
            conn.commit()
    
    def update_user_step(self, user_id: int, step: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE user_preferences SET current_step = ? WHERE user_id = ?', 
                (step, user_id)
            )
            # If no rows affected, insert a new record
            if cursor.rowcount == 0:
                cursor.execute(
                    'INSERT INTO user_preferences (user_id, current_step) VALUES (?, ?)',
                    (user_id, step)
                )
            conn.commit()

# API client
class ApiClient:
    def __init__(self, config: Config):
        self.config = config
        self.perplexity_client = None
        
        # Initialize API client
        if config.get("PERPLEXITY_API_KEY"):
            self.perplexity_client = OpenAI(
                api_key=config.get("PERPLEXITY_API_KEY"),
                base_url="https://api.perplexity.ai"
            )
            logger.info("Perplexity client initialized")
    
    async def is_api_online(self) -> bool:
        try:
            async with ClientSession() as session:
                prompts_url = f"{self.config.api_url}{self.config.api_methods.get('prompts', '')}"
                async with session.get(prompts_url) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error checking API url: {e}")
            return False
    
    async def generate_image(self, prompt: str) -> Optional[bytes]:
        """Generate an image using the provided prompt."""
        try:
            async with ClientSession() as session:
                prompt_data = FormData()
                prompt_data.add_field('prompt-text', prompt)
                gen_url = f"{self.config.api_url}{self.config.api_methods.get('gen', '')}"
                async with session.post(
                    gen_url,
                    data=prompt_data
                ) as response:
                    if response.status == 200:
                        return await response.read()
                    logger.error(f"API request failed with status {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error generating image: {e}")
            return None
    
    async def enhance_prompt(self, prompt: str) -> Tuple[str, str]:
        """Use AI to enhance a prompt and generate a title."""
        title = "your dream villa"
        enhanced_prompt = prompt
        
        if not self.perplexity_client:
            return prompt, title
            
        try:
            # Prepare messages for AI
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an artificial intelligence assistant and you need to "
                        "engage in a helpful, detailed, polite conversation with a user."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "You are an AI assistant tasked with processing messages from a Telegram channel and generating Stable Diffusion prompts based on the content. Each message contains a text input. Your job is to analyze the text input and create a prompt that will create an original photo using Stable Diffusion."
                        "You work on architecture project, keep in mind your results will be shown to final customers and architect teams."
                        "Emphasis on lightness of structures."
                        "You will receive a text input:"
                        f"<text_input>{prompt}</text_input>"
                        "Follow these steps to process the input and generate a Stable Diffusion prompt:"
                        "1. Interpret the prompt:"
                        "   - Identify key words, themes, or concepts mentioned in the legend."
                        "   - Determine the mood, tone, or atmosphere suggested by the text."
                        "2. Generate a Stable Diffusion prompt:"
                        "   - Incorporate elements from the legend to guide the modification or enhancement of the image."
                        "   - Use specific, descriptive language to convey the desired style, mood, and visual elements."
                        "   - Include any relevant techniques, or references that align with the legend and original photo."
                        "3. Refine and optimize the prompt:"
                        "   - Ensure the prompt is clear, concise, and focused."
                        "   - Use Stable Diffusion-friendly terminology and structure."
                        "   - Balance faithfulness to the original photo with creative interpretation of the legend."
                        "4. Give a title for the work you have done:"
                        "   - the title should explain in 5-10 words what will be visible on the image."
                        "   - the title will be used as the caption for the generated image."
                        "   - try to be funny, but don't overthink it: you are a clown that can make serious people laugh!"
                        "Provide your output in the following format:"
                        "<result><analysis>"
                        "[Your analysis of the text_input]"
                        "</analysis>"
                        "<stable_diffusion_prompt>"
                        "[Your generated Stable Diffusion prompt]"
                        "</stable_diffusion_prompt>"
                        "<title>"
                        "[Your generated Title for this work]"
                        "</title></result>"
                        "Check that the <stable_diffusion_prompt> and <title> tags are available inside the response <result> tag."
                    ),
                },
            ]

            # Choose which client to use based on availability
            if self.perplexity_client:
                response = self.perplexity_client.chat.completions.create(
                    model="sonar",
                    messages=messages,
                )
                content = response.choices[0].message.content

            # Extract the stable diffusion prompt
            prompt_pattern = r'<stable_diffusion_prompt>(.*?)</stable_diffusion_prompt>'
            prompt_match = re.search(prompt_pattern, content, re.DOTALL)
            if prompt_match:
                enhanced_prompt = prompt_match.group(1).strip()

            # Extract the title
            title_pattern = r'<title>(.*?)</title>'
            title_match = re.search(title_pattern, content, re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()

        except Exception as e:
            logger.error(f"Error generating enhanced prompt: {e}")

        return enhanced_prompt, title

# Villa designer functionality
class VillaDesigner:
    def __init__(self, db: DatabaseManager, api_client: ApiClient):
        self.db = db
        self.api_client = api_client
        
        # Budget description mapping
        self.budget_desc = {
            "100k-200k": "budget-friendly",
            "200k-300k": "standard",
            "300k-500k": "premium",
            "500k-750k": "luxury",
            "750k-1m": "ultra-luxury",
            "1m-plus": "elite high-end"
        }
        
        # Camera angle description mapping
        self.angle_desc = {
            "orbit": "aerial 360-degree view around a",
            "top-down": "top-down aerial view of a",
            "approach": "front approach view of a",
            "flyover": "low flyover shot of a",
            "parallax": "parallax arc shot of a"
        }
    
    async def show_home_screen(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Display the villa configuration home screen with current parameters and options."""
        user_id = update.effective_user.id if update and update.effective_user else context._user_id
        
        # Get current user preferences
        prefs = self.db.get_user_preferences(user_id)
        
        # Create home screen keyboard with current values
        keyboard = [
            [InlineKeyboardButton(f"💰 Budget: ${prefs['budget']}", callback_data="edit:budget")],
            [InlineKeyboardButton(f"📍 Location: {prefs['location']}", callback_data="edit:location")],
            [InlineKeyboardButton(f"🎨 Style: {prefs['style']}", callback_data="edit:style")],
            [InlineKeyboardButton(f"📷 Camera Angle: {prefs['camera_angle']}", callback_data="edit:camera_angle")],
            [InlineKeyboardButton("🔄 Generate Villa", callback_data="action:generate")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "🏝️ *Dream Villa Designer*\n\nCustomize your villa parameters and click Generate when ready!"

        if update and update.callback_query:
            # Edit existing message if called from callback
            await update.callback_query.edit_message_text(
                text=message_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # Send new message
            await update.message.reply_text(
                text=message_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    
    async def start_generation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Start the villa generation process with a home screen interface."""
        user_id = update.effective_user.id
        
        # Initialize user preferences
        self.db.update_user_step(user_id, 'home')
        
        await self.show_home_screen(update, context)
    
    async def handle_button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data.split(':')
        action_type = data[0]
        action_value = data[1]
        
        if action_type == 'edit':
            # User wants to edit a parameter
            self.db.update_user_step(user_id, f"editing_{action_value}")
            
            if action_value == 'budget':
                # Show budget selection buttons
                keyboard = [
                    [InlineKeyboardButton("$100K-$200K (Budget)", callback_data="budget:100k-200k")],
                    [InlineKeyboardButton("$200K-$300K (Standard)", callback_data="budget:200k-300k")],
                    [InlineKeyboardButton("$300K-$500K (Premium)", callback_data="budget:300k-500k")],
                    [InlineKeyboardButton("$500K-$750K (Luxury)", callback_data="budget:500k-750k")],
                    [InlineKeyboardButton("$750K-$1M (Ultra-Luxury)", callback_data="budget:750k-1m")],
                    [InlineKeyboardButton("$1M+ (Elite)", callback_data="budget:1m-plus")],
                    [InlineKeyboardButton("« Back to Home", callback_data="action:home")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text="What's your budget range for the villa?",
                    reply_markup=reply_markup
                )
                
            elif action_value == 'location':
                # Show location selection buttons
                keyboard = [
                    [InlineKeyboardButton("Seaside", callback_data="location:seaside")],
                    [InlineKeyboardButton("Jungle", callback_data="location:jungle")],
                    [InlineKeyboardButton("Mountain", callback_data="location:mountain")],
                    [InlineKeyboardButton("Urban", callback_data="location:urban")],
                    [InlineKeyboardButton("« Back to Home", callback_data="action:home")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text="Where would you like your villa to be located?",
                    reply_markup=reply_markup
                )
                
            elif action_value == 'style':
                # Show style selection buttons
                keyboard = [
                    [InlineKeyboardButton("Modern", callback_data="style:modern")],
                    [InlineKeyboardButton("Rustic/Wood", callback_data="style:rustic")],
                    [InlineKeyboardButton("Mediterranean", callback_data="style:mediterranean")],
                    [InlineKeyboardButton("Minimalist", callback_data="style:minimalist")],
                    [InlineKeyboardButton("« Back to Home", callback_data="action:home")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text="What architectural style would you prefer for your villa?",
                    reply_markup=reply_markup
                )
                
            elif action_value == 'camera_angle':
                # Show camera angle selection buttons
                keyboard = [
                    [InlineKeyboardButton("Orbit (360° around property)", callback_data="camera_angle:orbit")],
                    [InlineKeyboardButton("Top-Down (Aerial view)", callback_data="camera_angle:top-down")],
                    [InlineKeyboardButton("Front Approach", callback_data="camera_angle:approach")],
                    [InlineKeyboardButton("Flyover (Low pass)", callback_data="camera_angle:flyover")],
                    [InlineKeyboardButton("Parallax Arc", callback_data="camera_angle:parallax")],
                    [InlineKeyboardButton("« Back to Home", callback_data="action:home")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text="Which camera angle would you like for your villa shot?",
                    reply_markup=reply_markup
                )
                
        elif action_type == 'action':
            if action_value == 'home':
                # Return to home screen
                self.db.update_user_step(user_id, 'home')
                await self.show_home_screen(update, context)
                
            elif action_value == 'generate':
                # Get all preferences to generate the prompt
                prefs = self.db.get_user_preferences(user_id)
                
                # Get descriptive text based on preferences
                budget_desc = self.budget_desc.get(prefs['budget'], "luxury")
                angle_desc = self.angle_desc.get(prefs['camera_angle'], "aerial view of a")
                
                # Create the base prompt
                prompt = f"A photorealistic {angle_desc} {budget_desc} {prefs['style']} villa located at the {prefs['location']}, luxury vacation home, professional photography, high detail, 4K"
                
                await query.edit_message_text(
                    text=f"Generating your dream villa...\n\n{prompt}"
                )
                
                # Enhance the prompt
                enhanced_prompt, title = await self.api_client.enhance_prompt(prompt)
                
                # Update the message with the enhanced prompt
                await query.edit_message_text(
                    text=f"Generating {title}...\n\n{enhanced_prompt}"
                )
                
                # Generate the image
                result_image = await self.api_client.generate_image(enhanced_prompt)
                
                if result_image:
                    # Send the generated image
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=result_image,
                        caption=title
                    )
                    
                    await query.message.edit_text(f"Here's {title}!\n\n{enhanced_prompt}")
                else:
                    await query.message.edit_text("Sorry, there was an error generating your villa. Please try again later.")
        
        elif action_type in ['budget', 'location', 'style', 'camera_angle']:
            # User selected a specific value for a parameter
            self.db.update_user_preference(user_id, action_type, action_value)
            self.db.update_user_step(user_id, 'home')
            
            # Return to home screen after setting a parameter
            await self.show_home_screen(update, context)

# Main bot class
class DreamVillaBot:
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager()
        self.api_client = ApiClient(self.config)
        self.villa_designer = VillaDesigner(self.db, self.api_client)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send welcome message when the command /start is issued."""
        if update.message:
            await update.message.reply_text(self.config.messages.get("start", "Welcome to the Dream Villa Bot!"))
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send help message when the command /help is issued."""
        if update.message:
            await update.message.reply_text(self.config.messages.get("help", "Send an image with a caption to process it."))
    
    async def info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send info message when the command /info is issued."""
        if update.message:
            reply_text = self.config.messages.get("info", "Dream Villa Bot")
            is_online = await self.api_client.is_api_online()
            
            if is_online:
                reply_text += "\n\n✅ API service available"
            else:
                reply_text += "\n\n❌ API service offline"
                
            await update.message.reply_text(reply_text)
    
    async def handle_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Welcome new members when they join."""
        if update.message and update.message.new_chat_members:
            for new_member in update.message.new_chat_members:
                if not new_member.is_bot:
                    await update.message.reply_text(
                        f"Welcome {new_member.mention_html()}!\n\n{self.config.messages.get('welcome', 'Welcome to the group!')}",
                        parse_mode='HTML'
                    )
    
    def run(self):
        """Start the bot."""
        # Create the Application
        application = Application.builder().token(self.config.bot_token).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("info", self.info_command))
        application.add_handler(CommandHandler("villa", self.villa_designer.start_generation))
        
        # Add message handlers
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.handle_new_member))

        # Add callback handlers
        application.add_handler(CallbackQueryHandler(
            self.villa_designer.handle_button_callback, 
            pattern="^(budget|location|style|camera_angle|edit|action):"
        ))
        
        # Run the bot
        application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    bot = DreamVillaBot()
    bot.run()

if __name__ == "__main__":
    main()
