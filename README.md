This project is a simple and safe code execution API built using Flask and Docker.
Users can submit Python code, and the server runs it inside a secure Docker container and returns the output.

This project was created for learning purposes based on the assignment requirements.

1. What the Project Does

Users send Python code like:

{
  "code": "print('Hello World')"
}


The API returns:

{
  "output": "Hello World"
}

<img width="1920" height="1008" alt="Screenshot 2025-12-10 154046" src="https://github.com/user-attachments/assets/d85e22ec-5507-4090-95bb-abdb264975f0" />

The code never runs directly on the host machine. It runs inside a secure Docker container.

2. Features Implemented (Assignment Requirements)
Basic Execution

Runs Python code inside a Docker container

Returns the captured output

Safety Features

Implemented as required:

Attack Attempt	Protection Added	Result
Infinite loop (while True)	Docker timeout (10 seconds)	Execution stops safely
Memory abuse (x = 'a' * 1,000,000,000)	128MB memory limit	Process is killed
Internet access (requests.get())	--network none	Network blocked
Docker Flags Used
--network none
--memory=128m
--timeout 10      (via subprocess timeout)
--read-only
<img width="1920" height="1008" alt="Screenshot 2025-12-09 144104" src="https://github.com/user-attachments/assets/3fda2aef-6a9c-4416-89f0-16fda38d42f3" />

<img width="1920" height="1008" alt="Screenshot 2025-12-09 144006" src="https://github.com/user-attachments/assets/f2419d6d-fc25-4b69-ba2c-48a20280c9ae" />


<img width="1920" height="1008" alt="Screenshot 2025-12-09 145237" src="https://github.com/user-attachments/assets/a234a15b-4e32-4296-afb6-39ddfbe581f7" />


3. How It Works Internally

API receives Python code at the /run endpoint.

The code is executed inside a Docker container using Python 3.11.

Docker isolates the environment using:

Memory limits

No network

Timeout control

Read-only filesystem

Output and errors are captured.

The result is returned in JSON format.

<img width="1920" height="1008" alt="Screenshot 2025-12-10 161220" src="https://github.com/user-attachments/assets/8ba824c7-7813-4666-8ea4-1383c6f0f38d" />


4. Project Structure
safe-code-executor/
│
├── app.py               # Flask API server
├── templates/
│   └── index.html       # Simple user interface
└── logs/
    └── executions.log   # Saved execution history

5. Running the Project
Install dependencies
pip install flask

Ensure Docker is installed
docker --version

Start the server
python app.py


Server will be available at:

http://127.0.0.1:5000

6. Example API Calls
Run normal code
print("Hello")

Infinite loop test (should stop after 10 seconds)
while True:
    pass

Memory abuse test
x = "a" * 1000000000

Network access test (should fail)
import requests
requests.get("http://example.com")

Example curl request
curl -X POST http://localhost:5000/run \
    -H "Content-Type: application/json" \
    -d "{\"code\": \"print('Hello')\"}"

7. Docker Security Experiments
Test 1: Reading /etc/passwd
with open("/etc/passwd") as f:
    print(f.read())


Result:
Works, because it reads the container's /etc/passwd file, not the host's.

Lesson:
Docker isolates filesystem, but containers still contain their own system files.

Test 2: Writing to /tmp without read-only
with open("/tmp/test.txt", "w") as f:
    f.write("hacked!")


Result:
Works when the filesystem is writable.

After enabling --read-only, this code fails with a write error.

Lesson:
--read-only protects the container filesystem.

Test 3: Internet access attempt
import requests
requests.get("http://evil.com")


Result:
Fails because the container runs with --network none.

Lesson:
Disabling network access prevents malicious outbound requests.

8. What I Learned

Running untrusted code is dangerous and requires strict sandboxing.

Docker helps isolate execution using:

Memory limits

CPU control

No network

Read-only filesystem

Docker does not guarantee complete security because it shares the host kernel.

Proper timeouts prevent infinite loops from crashing the system.

Clear documentation is essential for users to understand the system and its limits.

9. Web UI

A simple HTML page (index.html) was created containing:

A textarea for entering Python code

A Run button

A section to display output or errors

This helps non-technical users test the service easily.
