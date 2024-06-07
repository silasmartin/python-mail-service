FROM python:3.12
LABEL Maintainer="Silas Martin <mail@silasmartin.de>" \
  Description="Simple mail service to send form notification mails"

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN apt update
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD [ "python", "./main.py" ]