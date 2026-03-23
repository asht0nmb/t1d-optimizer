import os
from dotenv import load_dotenv
from tconnectsync.api.tandemsource import TandemSourceApi

load_dotenv()
api = TandemSourceApi(email=os.getenv('TCONNECT_EMAIL'), password=('TCONNECT_PASSWORD'))

pump_info = api.pumper_info()
