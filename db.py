from pymongo import MongoClient


client = MongoClient("mongodb+srv://tinhnx:Xuantinh2212@tnx.qvkj3e2.mongodb.net/?retryWrites=true&w=majority&appName=Tnx")
db = client.tnxp
polls = db.polls


