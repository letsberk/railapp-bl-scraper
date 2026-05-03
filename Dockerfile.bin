FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements_bl.txt .
RUN pip install -r requirements_bl.txt

COPY betrieblive_scraper.py .

CMD ["python", "betrieblive_scraper.py"]
