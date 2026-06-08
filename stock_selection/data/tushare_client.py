import os
import tushare as ts
pro = ts.pro_api('9220ad7e63f02f54f058ca72be429e3e03b9608f960ca5ddd8a19b00')
pro._DataApi__http_url = "http://minitick.top/"
df = pro.index_basic(limit=5)
print(df)
#使用pro_bar接口请按下列方式加上api=pro
df = ts.pro_bar(api=pro, ts_code="000001.SZ", limit=3)
print(df)
