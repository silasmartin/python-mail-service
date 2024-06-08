docker build . --tag pythonmailservice

docker run -d --restart unless-stopped --publish 8004:5000 pythonmailservice
