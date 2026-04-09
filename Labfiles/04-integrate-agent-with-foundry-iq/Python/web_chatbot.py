import os
import base64
import uuid
import re
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file, abort
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="static")

OUTPUT_DIR = Path("agent_outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

project_endpoint = os.environ.get("PROJECT_ENDPOINT")
agent_name_env = os.environ.get("AGENT_NAME", "product-expert-agent")

_openai_client = None
_agent = None
_initialized = False

# session_id -> { "conversation_id": str, "pending_approval": dict | None }
conversations: dict[str, dict] = {}

SAFE_FILENAME_RE = re.compile(r"^[\w\-. ]+$")


def get_clients():
    global _openai_client, _agent, _initialized
    if _initialized:
        return _openai_client, _agent

    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    if not project_endpoint:
        raise RuntimeError("PROJECT_ENDPOINT is not set in .env")

    credential = DefaultAzureCredential(
        exclude_environment_credential=True,
        exclude_managed_identity_credential=True,
    )
    project_client = AIProjectClient(credential=credential, endpoint=project_endpoint)
    _openai_client = project_client.get_openai_client()
    _agent = project_client.agents.get(agent_name=agent_name_env)
    _initialized = True
    print(f"Agent loaded: {_agent.name} (id: {_agent.id})")
    return _openai_client, _agent


def save_image(image_data: str, filename: str) -> str:
    filepath = OUTPUT_DIR / filename
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(image_data))
    return filename


def extract_response(response):
    texts = []
    images = []
    files = []
    approval = None

    # Check for MCP approval requests first
    if hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "type") and item.type == "mcp_approval_request":
                import json as _json
                try:
                    args = _json.loads(item.arguments)
                    args_display = _json.dumps(args, indent=2)
                except Exception:
                    args_display = str(item.arguments)
                approval = {
                    "id": item.id,
                    "name": getattr(item, "name", "Unknown tool"),
                    "server_label": getattr(item, "server_label", ""),
                    "arguments": args_display,
                }
                break

    if approval:
        return {"text": "", "images": [], "files": [], "approval": approval}

    # Simple text reply
    if hasattr(response, "output_text") and response.output_text:
        texts.append(response.output_text)
    elif hasattr(response, "output") and response.output:
        for item in response.output:
            if hasattr(item, "text") and item.text:
                texts.append(item.text)
            elif hasattr(item, "type"):
                if item.type == "image":
                    fname = f"chart_{uuid.uuid4().hex[:8]}.png"
                    if hasattr(item, "image") and hasattr(item.image, "data"):
                        save_image(item.image.data, fname)
                        images.append(fname)
                elif item.type == "file":
                    files.append("generated_file")

    # File citations
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
                oc, _ = get_clients()
                file_content = oc.containers.files.content.retrieve(
                    file_id=annotation.file_id,
                    container_id=annotation.container_id,
                )
                filepath = OUTPUT_DIR / annotation.filename
                with open(filepath, "wb") as f:
                    f.write(file_content.read())
                files.append(annotation.filename)

    return {
        "text": "\n\n".join(texts) if texts else "I couldn't generate a response.",
        "images": images,
        "files": files,
        "approval": None,
    }


# --- Routes ---

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/conversation", methods=["POST"])
def create_conversation():
    openai_client, _ = get_clients()
    conv = openai_client.conversations.create(items=[])
    session_id = uuid.uuid4().hex
    conversations[session_id] = {
        "conversation_id": conv.id,
        "pending_approval": None,
    }
    return jsonify({"session_id": session_id})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    session_id = data.get("session_id", "")
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Message is empty"}), 400

    openai_client, agent = get_clients()

    if session_id not in conversations:
        conv = openai_client.conversations.create(items=[])
        session_id = uuid.uuid4().hex
        conversations[session_id] = {
            "conversation_id": conv.id,
            "pending_approval": None,
        }

    conversation_id = conversations[session_id]["conversation_id"]

    openai_client.conversations.items.create(
        conversation_id=conversation_id,
        items=[{"type": "message", "role": "user", "content": user_message}],
    )

    response = openai_client.responses.create(
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
        input="",
    )

    result = extract_response(response)
    result["session_id"] = session_id

    if result.get("approval"):
        conversations[session_id]["pending_approval"] = result["approval"]

    return jsonify(result)


@app.route("/api/approve", methods=["POST"])
def approve():
    data = request.get_json()
    session_id = data.get("session_id", "")
    approved = data.get("approve", False)

    if session_id not in conversations:
        return jsonify({"error": "Invalid session"}), 400

    session = conversations[session_id]
    pending = session.get("pending_approval")
    if not pending:
        return jsonify({"error": "No pending approval"}), 400

    openai_client, agent = get_clients()
    conversation_id = session["conversation_id"]

    openai_client.conversations.items.create(
        conversation_id=conversation_id,
        items=[{
            "type": "mcp_approval_response",
            "approval_request_id": pending["id"],
            "approve": approved,
        }],
    )

    session["pending_approval"] = None

    response = openai_client.responses.create(
        conversation=conversation_id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
        input="",
    )

    result = extract_response(response)
    result["session_id"] = session_id
    return jsonify(result)


@app.route("/api/files/<path:filename>")
def download_file(filename):
    # Path traversal protection
    if not SAFE_FILENAME_RE.match(filename):
        abort(403)
    filepath = (OUTPUT_DIR / filename).resolve()
    if not filepath.is_relative_to(OUTPUT_DIR.resolve()):
        abort(403)
    if not filepath.exists():
        abort(404)
    return send_file(filepath)


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("  Contoso Product Expert Chatbot is running!")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
