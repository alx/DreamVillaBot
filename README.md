# Dream Villa Designer Telegram Bot

![alt text](https://github.com/alx/DreamVillaBot/blob/main/assets/villa.jpg?raw=true)

## Overview

Dream Villa Designer is a Telegram bot that allows users to customize and generate images of dream villas using AI. Users can specify parameters like budget, location, style, and camera angle to create personalized villa designs.

## Features

- **Villa Customization**: Users can select from various options for:
  - Budget (e.g., $100K-$200K, $200K-$300K, up to $1M+)
  - Location (e.g., Seaside, Jungle, Mountain, Urban)
  - Architectural Style (e.g., Modern, Rustic/Wood, Mediterranean, Minimalist)
  - Camera Angle (e.g., Orbit, Top-Down, Front Approach, Flyover, Parallax Arc)

- **AI-Powered Image Generation**: Utilizes advanced AI models to create photorealistic images of villas based on user preferences.

- **Interactive Interface**: Uses Telegram's inline keyboards for easy parameter selection and navigation.

## Setup

1. Clone the repository
   ```
   git clone https://github.com/alx/DreamVillaBot
   cd DreamVillaBot
   ```
2. Install required dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Set up a `config.json` file with your Telegram bot token and API credentials:
   ```
   cp config.sample.json config.json
   ```
4. Run the bot:
   ```
   python main.py
   ```

## Usage

1. Start a chat with the bot on Telegram
2. Use the `/villa` command to begin customizing your dream villa
3. Follow the prompts to select your preferences
4. Wait for the AI to generate your personalized villa image

## Dependencies

### api-call-matrix

Script use the Flask server from [api-call-matrix](https://github.com/alx/api-call-matrix/) project in order to generate images using WebUI Stable diffusion:

https://github.com/alx/api-call-matrix/blob/main/flask_server.py

### Python

- python-telegram-bot
- aiohttp
- openai
- sqlite3

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
