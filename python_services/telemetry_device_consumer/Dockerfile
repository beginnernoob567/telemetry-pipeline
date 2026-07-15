FROM python:3.11-slim
WORKDIR /consumer
COPY ../common /consumer/common
COPY . /consumer
ENV PYTHONPATH=/consumer/common:/consumer
RUN pip install -r requirements.txt
CMD ["python", "main.py"]