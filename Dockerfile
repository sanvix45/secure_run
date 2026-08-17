FROM python:3.10-slim

Install dependencies, add Google Chrome repository securely, and install Chrome

RUN apt-get update && apt-get install -y wget gnupg unzip && 

mkdir -p /etc/apt/keyrings && 

wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg && 

echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && 

apt-get update && apt-get install -y google-chrome-stable && 

apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "generic_extractor.py"]
