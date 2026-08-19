import ssl
import json
import requests
import aiohttp,asyncio
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from datetime import  datetime
from config import *
class TLSAdapter(HTTPAdapter):
    """
    Adapter that applies a custom SSLContext to urllib3 poolmanager.
    This preserves the intended behavior from your original sample.
    """

    def init_poolmanager(self,*args,**kwargs):
        context = create_urllib3_context()
        try:
            context.set_ciphers("DEFAULT@SECLEVEL=1")
        except Exception:
            pass
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args,**kwargs)


class Audit_Data:
    def __init__(self,logger, **kwargs):
        # assign incoming kwargs to attributes
        for key, value in kwargs.items():
            setattr(self, key, value)
        # headers from original code (kept as-is to avoid changing behavior)
        self.get_data_url=GET_DATA_URL
        self.key=KEY
        self.value=VALUE
        self.logger=logger
        self.logger.info(f"getdataurl:{self.get_data_url}")
        try:
            self.headers = {
                getattr(self, "key", None): getattr(self, "value", None),
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        except Exception as e:
            print(e)
            
        self.session = requests.Session()
        self.session.mount("https://", TLSAdapter())
        self._timeout=60
        self._verify=False
    def parse_date(self):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d","%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(self.evaluation_date, fmt)
            except ValueError:  
                continue
        raise ValueError(f"Date format not recognized: {self.evaluation_date}")
    def get_audit_details(self):
        """
        Fetch audit details with query params:
          - ProcessId -> self.process_id
          - TransactionDate -> self.evaluation_date
        Returns parsed JSON on success, raises exceptions on failures.
        """
        try: 
            dt =  self.parse_date()
            result = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
            params = {"ProcessId":str(self.process_id),"EvaluationDate":str(result),"AuditId":str(self.audit_id)}
            self.logger.info(f"Requesting audit details from URL:{self.get_data_url} and {params}")
           
            response = self.session.get(url=self.get_data_url, params=params, headers=self.headers,verify=self._verify,timeout=self._timeout)
        except Exception as e:
            self.logger.exception(f"Failed to fetch audit details,{str(e)}")
            response={}
            return response 
        try:
            if response.status_code==200:
                self.logger.info(f"Details fetched successfully")
                response_json=json.loads(response.text)
                # self.logger.info(f"Audit_details ,{response_json}")
            else:
                self.logger.error(f"Audit API failed ERROR:{response.status_code} - {response.text}")
                response_json={}
        except Exception as e:
            self.logger.exception(f"Unable to decode JSON:{str(e)}")
            response_json={}
        return response_json


    
    