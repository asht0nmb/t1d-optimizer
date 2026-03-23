from dotenv import load_dotenv
import os

TCONNECT_EMAIL=os.getenv('TCONNECT_EMAIL')
TCONNECT_PASSWORD=os.getenv('TCONNECT_PASSWORD')
TIMEZONE_NAME=os.getenv('TIMEZONE_NAME')

def main():
    print("Hello from t1d-engine!")


if __name__ == "__main__":
    main()
