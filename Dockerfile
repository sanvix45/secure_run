Use official Python lightweight image

FROM python:3.10-slim

Install system dependencies and Google Chrome

RUN apt-get update && apt-get install -y 

wget 

gnupg 

unzip 

&& wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - 

&& sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' 

&& apt-get update 

&& apt-get install -y google-chrome-stable 

&& apt-get clean 

&& rm -rf /var/lib/apt/lists/*

Set up the working directory

WORKDIR /app

Copy requirement file and install Python packages

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

Copy the rest of your application code

COPY . .

Run the python script

CMD ["python", "generic_extractor.py"]
