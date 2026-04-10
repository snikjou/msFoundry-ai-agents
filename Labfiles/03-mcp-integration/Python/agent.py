# 'os' lets us read settings stored in environment variables on our computer
import os
# 'load_dotenv' reads key-value pairs from a .env file and makes them available as environment variables
from dotenv import load_dotenv

# Add references
# DefaultAzureCredential handles logging in to Azure automatically using available credentials
from azure.identity import DefaultAzureCredential
# AIProjectClient lets us talk to our Azure AI Foundry project (create agents, manage resources, etc.)
from azure.ai.projects import AIProjectClient
# PromptAgentDefinition defines what our agent looks like (its model, instructions, and tools)
# MCPTool represents a remote MCP (Model Context Protocol) server that our agent can call
from azure.ai.projects.models import PromptAgentDefinition, MCPTool
# McpApprovalResponse is used to approve or deny MCP tool calls that the agent wants to make
# ResponseInputParam is the type for the list of inputs we send back to the agent
from openai.types.responses.response_input_param import McpApprovalResponse, ResponseInputParam


# Load environment variables from .env file so we can use them below
load_dotenv()
# Get the URL of our Azure AI Foundry project (this tells the code where our project lives)
project_endpoint = os.getenv("PROJECT_ENDPOINT")
# Get the name of the AI model we want to use (e.g., gpt-4o)
model_deployment = os.getenv("MODEL_DEPLOYMENT_NAME")

# Connect to the agents client
# 'with' makes sure all connections are properly closed when we're done
# Step 1: Log in to Azure using our default credentials
# Step 2: Create a project client that connects to our Azure AI Foundry project
# Step 3: Get an OpenAI client from the project so we can send messages to agents
with (
    DefaultAzureCredential() as credential,
    AIProjectClient(endpoint=project_endpoint, credential=credential) as project_client,
    project_client.get_openai_client() as openai_client,
):

    # Initialize agent MCP tool
    # This sets up a connection to a remote MCP server hosted by Microsoft Learn
    # The agent will use this server to look up API documentation and specs
    # 'server_label' is a friendly name we give this tool so we can identify it later
    # 'server_url' is the web address of the MCP server
    # 'require_approval' set to "always" means we must approve every call before it runs
    mcp_tool = MCPTool(
        server_label="api-specs",
        server_url="https://learn.microsoft.com/api/mcp",
        require_approval="always",
    )

    # Create a new agent with the MCP tool
    # This registers a new agent in our Azure AI Foundry project
    # The agent gets a name, an AI model to use, instructions on how to behave,
    # and a list of tools it can use (in this case, our MCP tool)
    agent = project_client.agents.create_version(
        agent_name="MyAgent",
        definition=PromptAgentDefinition(
            model=model_deployment,
            instructions="You are a helpful agent that can use MCP tools to assist users. Use the available MCP tools to answer questions and perform tasks.",
            tools=[mcp_tool],
        ),
    )
    # Print out the agent's details so we know it was created successfully
    print(f"Agent created (id: {agent.id}, name: {agent.name}, version: {agent.version})")

    # Create a new conversation thread
    # A conversation is like a chat session - it keeps track of all the messages
    # between us and the agent so the agent remembers the context
    conversation = openai_client.conversations.create()
    print(f"Created conversation (id: {conversation.id})")

    # Send our first question to the agent
    # This message asks about Azure CLI commands, which will cause the agent
    # to call the MCP tool (Microsoft Learn API) to look up the right documentation
    # 'conversation' links this message to our chat session
    # 'input' is the question we are asking
    # 'extra_body' tells the service which agent should handle this request
    response = openai_client.responses.create(
        conversation=conversation.id,
        input="What is Microsoft Agent Framework?.",
        extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
    )

    # Process any MCP approval requests that were generated
    # Because we set require_approval to "always", the agent pauses and asks us
    # for permission before calling the MCP server. Here we check the response
    # for any approval requests and automatically approve them.
    input_list: ResponseInputParam = []
    # Loop through each item in the agent's response
    for item in response.output:
        # Check if this item is an MCP approval request (the agent wants to call the MCP tool)
        if item.type == "mcp_approval_request":
            # Make sure it's from our "api-specs" MCP server and has a valid ID
            if item.server_label == "api-specs" and item.id:
                # Automatically approve the MCP request so the agent can go ahead and use the tool
                input_list.append(
                    McpApprovalResponse(
                        type="mcp_approval_response",
                        approve=True,
                        approval_request_id=item.id,
                    )
                )

    # Print the list of approvals we're sending back (useful for debugging)
    print("Final input:")
    print(input_list)

    # Send the approval responses back to the agent so it can continue
    # 'input' contains our list of approvals
    # 'previous_response_id' links this to the earlier response so the agent knows
    #  which conversation and which pending tool calls we're approving
    response = openai_client.responses.create(
        input=input_list,
        previous_response_id=response.id,
        extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
    )

    # Print the agent's final answer after it used the MCP tool to get the information
    print(f"\nAgent response: {response.output_text}")

    # Clean up resources by deleting the agent version we created
    # This is good practice so we don't leave unused agents in our project
    project_client.agents.delete_version(agent_name=agent.name, agent_version=agent.version)
    print("Agent deleted")