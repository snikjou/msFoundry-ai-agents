# Import the libraries we need
import os
from azure.ai.projects import AIProjectClient  # Talks to Azure AI Foundry
from azure.identity import DefaultAzureCredential  # Handles login to Azure
import base64  # Helps convert image data
from pathlib import Path  # Makes working with file paths easier
from dotenv import load_dotenv  # Reads settings from a .env file


def save_image(image_data, filename):
    """Save base64 image data to a file."""
    # Create a folder called "agent_outputs" if it doesn't exist yet
    output_dir = Path("agent_outputs")
    output_dir.mkdir(exist_ok=True)

    filepath = output_dir / filename

    # Turn the base64 text into real image bytes and save to a file
    image_bytes = base64.b64decode(image_data)
    with open(filepath, 'wb') as f:
        f.write(image_bytes)

    return str(filepath)


def main():
    # Load settings from .env file (like the project URL and agent name)
    load_dotenv()
    project_endpoint = os.environ.get("PROJECT_ENDPOINT")
    agent_name = os.environ.get("AGENT_NAME", "it-support-agent")

    if not project_endpoint:
        print("Error: PROJECT_ENDPOINT environment variable not set")
        print("Please set it in your .env file or environment")
        return

    # Sign in to Azure and connect to the Foundry project
    print("Connecting to Microsoft Foundry project...")
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(
        credential=credential,
        endpoint=project_endpoint
    )

    # Get the OpenAI client so we can send messages and get replies
    openai_client = project_client.get_openai_client()

    # Find and load the agent we already created in the Foundry portal
    print(f"Loading agent: {agent_name}")
    agent = project_client.agents.get(agent_name=agent_name)
    print(f"Connected to agent: {agent.name} (id: {agent.id})")

    # Start a new empty conversation (like opening a new chat window)
    conversation = openai_client.conversations.create(items=[])
    print(f"Conversation created (id: {conversation.id})")

    # Chat loop
    print("\n" + "="*60)
    print("IT Support Agent Ready!")
    print("Ask questions, request data analysis, or get help.")
    print("Type 'exit' to quit.")
    print("="*60 + "\n")

    # Keep chatting in a loop until the user types 'exit'
    while True:
        user_input = input("You: ").strip()

        # Stop the loop if user wants to quit
        if user_input.lower() in ['exit', 'quit', 'bye']:
            print("Goodbye!")
            break

        # Skip empty messages
        if not user_input:
            continue

        # Send the user's message to the conversation
        openai_client.conversations.items.create(
            conversation_id=conversation.id,
            items=[{"type": "message", "role": "user", "content": user_input}]
        )

        # Ask the agent to read the conversation and reply
        print("\n[Agent is thinking...]")
        response = openai_client.responses.create(
            conversation=conversation.id,
            extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
            input=""
        )

        # Show the agent's reply to the user
        if hasattr(response, 'output_text') and response.output_text:
            print(f"\nAgent: {response.output_text}\n")
        elif hasattr(response, 'output') and response.output:
            # The reply may have multiple parts (text, images, files) — go through each one
            image_count = 0
            for item in response.output:
                if hasattr(item, 'text') and item.text:
                    print(f"\nAgent: {item.text}\n")
                elif hasattr(item, 'type'):
                    # If the agent made a chart or image, save it to a file
                    if item.type == 'image':
                        image_count += 1
                        filename = f"chart_{image_count}.png"

                        # Download and save the image
                        if hasattr(item, 'image') and hasattr(item.image, 'data'):
                            filepath = save_image(item.image.data, filename)
                            print(f"\n[Agent generated a chart - saved to: {filepath}]")
                        else:
                            print(f"\n[Agent generated an image]")
                    elif item.type == 'file':
                        print(f"\n[Agent created a file]")

        # Check if the agent attached any files (like CSVs or reports)
        file_id = ""
        filename = ""
        container_id = ""

        # Look at the last message for any file links (called "citations")
        last_message = response.output[-1]
        if (
            last_message.type == "message"
            and last_message.content
            and last_message.content[-1].type == "output_text"
            and last_message.content[-1].annotations
        ):
            # Get the file ID and name from the citation info
            file_citation = last_message.content[-1].annotations[-1]
            if file_citation.type == "container_file_citation":
                file_id = file_citation.file_id
                filename = file_citation.filename
                container_id = file_citation.container_id

        # If there is an attached file, download and save it locally
        if file_id and filename:
            file_content = openai_client.containers.files.content.retrieve(file_id=file_id, container_id=container_id)
            output_dir = Path("agent_outputs")
            output_dir.mkdir(exist_ok=True)
            file_path = output_dir / filename
            with open(file_path, "wb") as f:
                f.write(file_content.read())
            print(f"File downloaded successfully: {file_path}")


if __name__ == "__main__":
    main()