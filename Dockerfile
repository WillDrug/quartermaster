FROM python:3.9
RUN pip install -r requirements.txt
COPY src /

CMD "python main.py"