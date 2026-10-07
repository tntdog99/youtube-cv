

setup:  
make google cloud account  
make new project  
enable youtube api with /auth/youtube.force-ssl  
make oauth with /auth/youtube.force-ssl perms  
save the json of the client secrets, never share this  
save it into this folder as credentials.json  
make venv  
```bash
python -m venv venv
```
install requirements
```bash
pip install -r requirements.txt
```
activate venv  
run program:
```bash
python main.py
```
press **h** to get help  