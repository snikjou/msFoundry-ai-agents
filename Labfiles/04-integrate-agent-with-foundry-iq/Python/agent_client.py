# -- Import the libraries we need --

import os                                      # To read environment variables (like settings)
from dotenv import load_dotenv                 # To load settings from a .env file
from azure.identity import DefaultAzureCredential  # To log in to Azure automatically
from azure.ai.projects import AIProjectClient      # To talk to our AI project on Azure

# -- Load settings from the .env file --
# The .env file has secrets like the project URL and agent name.
# load_dotenv() reads that file and makes those values available.
load_dotenv()
project_endpoint = os.getenv("PROJECT_ENDPOINT")  # The URL of our Azure AI project
agent_name = os.getenv("AGENT_NAME")              # The name of the agent we want to talk to

# -- Make sure the settings are there --
# If either value is missing, stop the program right away with an error message.
if not project_endpoint or not agent_name:
    raise ValueError("PROJECT_ENDPOINT and AGENT_NAME must be set in .env file")

# Print out which project and agent we're using (helpful for debugging)
print(f"Connecting to project: {project_endpoint}")
print(f"Using agent: {agent_name}\n")

# -- Connect to our Azure AI project --
# First, we create a "credential" — think of it like a login pass.
# DefaultAzureCredential tries several ways to log in (browser, CLI, etc.).
# We turn off two methods we don't need here to keep things simple.
credential = DefaultAzureCredential(
    exclude_environment_credential=True,       # Don't use environment variable credentials
    exclude_managed_identity_credential=True    # Don't use managed identity (for VMs/containers)
)

# Now we create the project client — this is our main connection to Azure AI.
# We give it our login pass (credential) and the project URL (endpoint).
project_client = AIProjectClient(
    credential=credential,
    endpoint=project_endpoint
)

# -- Get the OpenAI client --
# The project client gives us an OpenAI-compatible client.
# This client lets us send messages and get responses using the conversations API.
openai_client = project_client.get_openai_client()

# -- Find our agent --
# Look up the agent by its name (from the .env file).
# The agent is the AI "brain" that will answer our questions.
agent = project_client.agents.get(agent_name=agent_name)
print(f"Connected to agent: {agent.name} (id: {agent.id})\n")

# -- Start a new conversation --
# A conversation is like a chat room — it keeps track of all messages.
# We start with an empty list of items (no messages yet).
conversation = openai_client.conversations.create(items=[])
print(f"Created conversation (id: {conversation.id})\n")


# -- Keep track of the conversation on our side --
# This list stores every message (from the user and the agent) so we can show it later.
conversation_history = []


def send_message_to_agent(user_message):
    """
    Send a message to the agent and get back its reply.
    This uses the Azure conversations API to communicate with the agent.
    """
    try:
        # Show what the user typed
        print(f"You: {user_message}\n")
        print("Agent: ", end="", flush=True)  # Print "Agent: " without a newline, so the reply appears on the same line
        
        # -- Send the user's message to Azure --
        # We add the user's message to the conversation on the server.
        # "role": "user" means this message came from the human, not the agent.
        openai_client.conversations.items.create(
            conversation_id=conversation.id,
            items=[{"type": "message", "role": "user", "content": user_message}],
        )

        # -- Save a copy of the message locally --
        # We keep our own list so we can show the full chat history later.
        conversation_history.append({
            "role": "user",
            "content": user_message
        })

        # -- Ask the agent to respond --
        # We tell Azure which conversation to use and which agent should answer.
        # "input" is empty because the user's message is already in the conversation.
        # "agent_reference" tells Azure which specific agent should handle this.
        response = openai_client.responses.create(
            conversation=conversation.id,
            extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
            input=""
        )

        # -- Check if the agent needs permission to use an external tool (MCP) --
        # MCP (Model Context Protocol) lets the agent call outside tools/services.
        # Some MCP tools need the user to say "yes" before the agent can use them.
        # Here we look through the response to see if there's an approval request.
        approval_request = None
        if hasattr(response, 'output') and response.output:
            for item in response.output:                          # Look at each piece of output
                if hasattr(item, 'type') and item.type == 'mcp_approval_request':  # Is it asking for permission?
                    approval_request = item                       # Save the approval request
                    break                                         # Stop looking — we found one

        # -- If the agent needs permission, ask the user --
        if approval_request:
            # Show which MCP tool is asking for permission and which server it's from
            print(f"[Approval required for: {approval_request.name}]\n")
            print(f"Server: {approval_request.server_label}")

            # -- Show the details of what the tool wants to do --
            # The "arguments" tell us exactly what data the tool will use.
            # We try to format it nicely (indented JSON), but if that fails,
            # we just show the raw text.
            import json
            try:
                args = json.loads(approval_request.arguments)       # Turn the text into a Python dict
                print(f"Arguments: {json.dumps(args, indent=2)}\n")  # Print it nicely with spacing
            except:
                print(f"Arguments: {approval_request.arguments}\n")  # Fallback: print as-is

            # -- Ask the user: "Is this okay?" --
            # The user gets to decide if the agent is allowed to run this tool.
            approval_input = input("Approve this action? (yes/no): ").strip().lower()

            if approval_input in ['yes', 'y']:
                # User said yes — build an approval message to send back
                print("Approving action...\n")
                approval_response = {
                    "type": "mcp_approval_response",          # This tells Azure it's an approval answer
                    "approval_request_id": approval_request.id,  # Links back to the original request
                    "approve": True                            # True = "go ahead, I allow it"
                }
            else:
                # User said no — build a denial message to send back
                print("Action denied.\n")
                approval_response = {
                    "type": "mcp_approval_response",          # Same type, but...
                    "approval_request_id": approval_request.id,  # ...same request ID...
                    "approve": False                           # False = "no, don't do it"
                }

            # -- Send the approval/denial back to the conversation --
            # This tells the agent whether it can go ahead with the tool or not.
            openai_client.conversations.items.create(
                conversation_id=conversation.id,
                items=[approval_response]
            )

            # -- Now ask the agent to respond again --
            # After we approve or deny, the agent will either use the tool result
            # or explain that the action was blocked. We get the final answer here.
            response = openai_client.responses.create(
                conversation=conversation.id,
                extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
                input=""
            )


        
        
        # -- Handle the agent's response --
        # Check if we got a reply and it has text in it
        if response and response.output_text:
            response_text = response.output_text  # Get the actual text of the reply
            
            print(f"{response_text}\n")  # Print the agent's answer
            
            # -- Show citations (sources) if the agent used any --
            # Citations tell us where the agent found its information (e.g., a product catalog)
            if hasattr(response, 'citations') and response.citations:
                print("\nSources:")
                for citation in response.citations:
                    print(f"  - {citation.content if hasattr(citation, 'content') else 'Knowledge Base'}")
            
            # Save the agent's reply in our local history list
            conversation_history.append({
                "role": "assistant",
                "content": response_text
            })
            
            return response_text  # Return the reply so other code can use it
        else:
            # If the agent didn't send back anything, let the user know
            print("No response received.\n")
            return None
    except Exception as e:
        # If something goes wrong, print the error instead of crashing
        print(f"\n\nError: {str(e)}\n")
        return None


def display_conversation_history():
    """
    Show the full chat history — every message from the user and the agent.
    """
    print("\n" + "="*60)
    print("CONVERSATION HISTORY")
    print("="*60 + "\n")
    
    # Loop through each message and print it with the role (USER or ASSISTANT)
    for turn in conversation_history:
        role = turn["role"].upper()    # "user" becomes "USER", "assistant" becomes "ASSISTANT"
        content = turn["content"]
        print(f"{role}: {content}\n")
    
    print("="*60 + "\n")


def main():
    """
    The main loop — keeps asking the user for input and sending it to the agent.
    Runs until the user types 'quit' or presses Ctrl+C.
    """
    # Welcome message
    print("Contoso Product Expert Agent")
    print("Ask questions about our outdoor and camping products.")
    print("Type 'history' to see conversation history, or 'quit' to exit.\n")
    
    # Keep looping forever until the user decides to stop
    while True:
        try:
            # Wait for the user to type something
            user_input = input("You: ").strip()  # .strip() removes extra spaces from the start/end
            
            # If the user just pressed Enter without typing, skip and ask again
            if not user_input:
                continue
            
            # If the user typed 'quit', end the conversation
            if user_input.lower() == 'quit':
                print("\nEnding conversation...")
                break
            
            # If the user typed 'history', show all past messages
            if user_input.lower() == 'history':
                display_conversation_history()
                continue
            
            # Otherwise, send the message to the agent and get a response
            send_message_to_agent(user_input)
            
        except KeyboardInterrupt:
            # If the user presses Ctrl+C, stop gracefully
            print("\n\nInterrupted by user.")
            break
        except Exception as e:
            # Catch any other unexpected errors and keep going
            print(f"\nUnexpected error: {str(e)}\n")
    
    print("\nConversation ended.")


# -- Start the program --
# This runs the main() function only when you run this file directly (not when importing it)
if __name__ == "__main__":
    main()
