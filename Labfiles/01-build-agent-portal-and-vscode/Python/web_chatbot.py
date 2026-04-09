# Web chatbot that connects to the same Azure AI Foundry agent
import os
import base64
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file
from dotenv import load_dotenv

# Load settings from .env file
load_dotenv()

app = Flask(__name__, static_folder="static")

# Folder to save any files the agent generates (images, CSVs, etc.)
OUTPUT_DIR = Path("agent_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

# --- Lazy connection to Azure AI Foundry (connects on first request, not at import) ---

project_endpoint = os.environ.get("PROJECT_ENDPOINT")
agent_name_env = os.environ.get("AGENT_NAME", "it-support-agent")

# These will be set on first request
_openai_client = None
_agent = None
_initialized = False

# Store active conversations in memory (session_id -> openai conversation id)
conversations: dict[str, str] = {}


def get_clients():
    """Connect to Azure AI Foundry lazily on first use."""
    global _openai_client, _agent, _initialized
    if _initialized:
        return _openai_client, _agent

    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    if not project_endpoint:
        raise RuntimeError("PROJECT_ENDPOINT is not set. Add it to .env or App Service settings.")

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(credential=credential, endpoint=project_endpoint)
    _openai_client = project_client.get_openai_client()
    _agent = project_client.agents.get(agent_name=agent_name_env)
    _initialized = True
    print(f"Agent loaded: {_agent.name} (id: {_agent.id})")
    return _openai_client, _agent


# --- Helper functions ---

def save_image(image_data: str, filename: str) -> str:
    """Save a base64-encoded image to disk and return the file path."""
    filepath = OUTPUT_DIR / filename
    image_bytes = base64.b64decode(image_data)
    with open(filepath, "wb") as f:
        f.write(image_bytes)
    return filename


def extract_response(response):
    """Pull text, images, and file downloads out of the agent's response."""
    texts = []
    images = []
    files = []
    image_count = 0

    # 1. Simple text reply
    if hasattr(response, "output_text") and response.output_text:
        texts.append(response.output_text)

    # 2. Multi-part reply (text + images + files)
    elif hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "text") and item.text:
                texts.append(item.text)
            elif hasattr(item, "type"):
                if item.type == "image":
                    image_count += 1
                    fname = f"chart_{uuid.uuid4().hex[:8]}.png"
                    if hasattr(item, "image") and hasattr(item.image, "data"):
                        save_image(item.image.data, fname)
                        images.append(fname)
                elif item.type == "file":
                    files.append("generated_file")

    # 3. Check for file citations (attached CSVs, reports, etc.)
    if hasattr(response, "output") and response.output:
        last_message = response.output[-1]
        if (
            getattr(last_message, "type", None) == "message"
            and getattr(last_message, "content", None)
            and getattr(last_message.content[-1], "type", None) == "output_text"
            and getattr(last_message.content[-1], "annotations", None)
        ):
            annotation = last_message.content[-1].annotations[-1]
            if getattr(annotation, "type", None) == "container_file_citation":
                file_id = annotation.file_id
                filename = annotation.filename
                container_id = annotation.container_id
                # Download the file from the agent's container
                oc, _ = get_clients()
                file_content = oc.containers.files.content.retrieve(
                    file_id=file_id, container_id=container_id
                )
                filepath = OUTPUT_DIR / filename
                with open(filepath, "wb") as f:
                    f.write(file_content.read())
                files.append(filename)

    return {
        "text": "\n\n".join(texts) if texts else "I couldn't generate a response.",
        "images": images,
        "files": files,
    }


# --- Routes ---

@app.route("/")
def index():
    """Serve the chat page."""
    return send_from_directory("static", "index.html")


@app.route("/api/conversation", methods=["POST"])
def create_conversation():
    """Start a new chat conversation and return its ID."""
    openai_client, _ = get_clients()
    conv = openai_client.conversations.create(items=[])
    session_id = uuid.uuid4().hex
    conversations[session_id] = conv.id
    return jsonify({"session_id": session_id})


@app.route("/api/chat", methods=["POST"])
def chat():
    """Send a message and get the agent's reply."""
    data = request.get_json()
    session_id = data.get("session_id", "")
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Message is empty"}), 400

    openai_client, agent = get_clients()

    # Find or create a conversation
    if session_id not in conversations:
        conv = openai_client.conversations.create(items=[])
        session_id = uuid.uuid4().hex
        conversations[session_id] = conv.id

    conversation_id = conversations[session_id]

    # Add the user's message to the conversation
    openai_client.conversations.items.create(
        conversation_id=conversation_id,
        items=[{"type": "message", "role": "user", "content": user_message}],
    )

    # Ask the agent to reply
    response = openai_client.responses.create(
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
        input="",
    )

    # Parse the agent's response into text, images, and files
    result = extract_response(response)
    result["session_id"] = session_id
    return jsonify(result)


@app.route("/api/files/<path:filename>")
def download_file(filename):
    """Let the browser download a file the agent created."""
    filepath = OUTPUT_DIR / filename
    if not filepath.resolve().is_relative_to(OUTPUT_DIR.resolve()):
        return jsonify({"error": "Invalid path"}), 403
    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath)


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  IT Support Chatbot is running!")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
