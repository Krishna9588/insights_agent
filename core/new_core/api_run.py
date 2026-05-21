# api_key = input("enter your api key: ")

from google import genai

client = genai.Client(api_key="AIzaSyBeRKaPjciTHjTEHVfHKz0lz9aA02CFfo8")

try:
    response = client.models.generate_content(
        # model="gemini-2.5-flash",
        model="gemini-2.5-flash-lite",
        contents="Say the API is working!"
    )
    print(response.text)
except Exception as error:
    print(f"Error details: {error}")