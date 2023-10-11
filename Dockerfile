FROM python:3.12-alpine

WORKDIR /usr/src/teslamate-abrp

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create a non-root user
RUN adduser -D toor
USER toor

CMD [ "python", "-u", "./teslamate_mqtt2abrp.py" ]
