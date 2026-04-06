SAJ Crypto Trading Bot

SAJ Crypto Trading Bot is a multi-user SaaS trading platform that enables individuals and businesses to run automated cryptocurrency trading bots with ease.

It combines a modern frontend, secure backend, and scalable bot execution system to deliver a complete trading automation solution.

🚀 Overview

The platform allows users to:

Register and manage accounts securely
Connect exchange API keys
Select and configure trading strategies
Launch and manage trading bots
Receive Telegram notifications
Manage subscriptions or redeem vouchers

Admins can:

Manage users and roles
Control strategies and platform settings
Assign subscriptions and generate vouchers
Monitor bot activity across the platform
🧩 Key Features
Multi-user SaaS architecture
Secure API key encryption (Fernet)
Docker-based isolated bot execution
AI Runtime support for advanced strategies
Subscription & voucher monetization system
Telegram integration for real-time alerts
Admin dashboard for full system control
Backend-enforced bot launch validation
💡 What Makes It Unique
Built as a complete SaaS platform, not just a bot
Supports both Docker runtime and AI/local runtime
Includes built-in monetization (subscriptions & vouchers)
Designed for scalability and multi-user environments
👥 Target Users
Beginner traders seeking automation
Advanced users testing custom strategies
Businesses offering trading services
Developers building AI-powered trading systems
🏗️ System Architecture
Frontend: React 18
Backend: FastAPI
Bot Manager: Python service for runtime orchestration
Database: PostgreSQL 16
Execution: Docker + Hybrid Host Runtime
📂 Project Structure
saj_crypto_bot/
├─ backend/
├─ bot_manager/
├─ database/
├─ frontend/
├─ user_data/
├─ .env
├─ docker-compose.yml
🔐 Security
JWT authentication
Role-based access control
Encrypted API key storage
Backend validation for all bot launches
⚙️ Quick Start
git clone https://github.com/your-repo/saj_crypto_bot.git
cd saj_crypto_bot
cp .env.example .env
docker compose up --build -d
Default Ports
Frontend: 3000
Backend: 8000
Bot Manager: 8100
PostgreSQL: 5432
🧪 Health Checks
curl http://localhost:8000/health
curl http://localhost:8100/health
🤖 Bot Execution Modes
1. Docker Mode
Isolated per-user containers
Safe for production SaaS
2. AI Runtime Mode
Local strategy execution
Supports advanced/custom workflows
💳 Subscription System

Supports:

Free trials
Paid subscriptions
Admin-issued plans
Voucher-based access
📱 Telegram Integration
Centralized bot token (admin-managed)
User-specific chat IDs
Real-time trading notifications
📊 Use Cases
Launch your own crypto trading SaaS
Offer automated trading services
Run multiple bots for clients
Test AI trading strategies locally
📸 Screenshots

Add screenshots here (recommended):

Dashboard
Setup page
Admin panel
Bot status
🎥 Demo

Add your YouTube demo link here

🧑‍💻 Author

Eliud Karanja
📞 +254 729 576 473
📧 eliudkaranja5@gmail.com

💬 WhatsApp: +254 702 839 859

Services
Software Development
POS Systems
CCTV & Networking
WiFi Installation

📌 Status

Actively evolving into a full SaaS product with:

Improved onboarding
Enhanced UI/UX
Advanced analytics
Cloud deployment
