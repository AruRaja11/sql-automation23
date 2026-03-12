# virtual environment
VIRTUAL_ENVIRONMENT = "/home/arun-raja/Documents/VSC/.venv"

# AI Configuration
# Set to 'anthropic' or 'llama'
AI_PROVIDER = "llama" 
ANTHROPIC_API_KEY = "your-api-key-here"
LLAMA_MODEL_PATH = "/media/arun-raja/4C0A6CA10A6C8A32/hf_cache/hub/models--bczhou--tiny-llava-v1-hf/snapshots/70f28eb22f0265a8a41858f6aebc1928cedd53eb"

# Database Configuration (Default)
DB_TYPE = "mysql" # 'sqlite' or 'postgres' or 'mysql'
DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "database": "materials_db", # Leave empty if you want to connect to the server first
    "user": "root",
    "password": "Root@1234"
}


# llama model 
LLAMA_MODEL_NAME = "llama3.2:1b"
LLAMA_MODEL_PATH = "/media/arun-raja/4C0A6CA10A6C8A32/hf_cache/hub/models--bczhou--tiny-llava-v1-hf/snapshots/70f28eb22f0265a8a41858f6aebc1928cedd53eb"