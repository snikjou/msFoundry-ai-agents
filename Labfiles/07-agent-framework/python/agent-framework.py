# --- Import the libraries we need ---

# 'os' lets us work with the operating system (e.g. clear the screen, read settings)
import os
# 'asyncio' lets us run code that waits for things (like API calls) without freezing
import asyncio
# 'Path' helps us build file paths that work on any operating system
from pathlib import Path

# 'tool' is a decorator that turns a regular function into a tool the agent can call
# 'Agent' is the main class that represents our AI agent
from agent_framework import tool, Agent
# This client connects our agent to an Azure OpenAI model
from agent_framework.azure import AzureOpenAIResponsesClient
# This lets us log in to Azure using the Azure CLI credentials on our machine
from azure.identity import AzureCliCredential
# 'Field' lets us add descriptions to function parameters so the agent knows what they mean
from pydantic import Field
# 'Annotated' lets us attach extra info (like Field descriptions) to parameter types
from typing import Annotated


async def main():
    """Starting point of the program. Reads expense data from a file
    and asks the user what they want to do with it."""

    # Clear the console so the output looks clean
    os.system('cls' if os.name=='nt' else 'clear')

    # Find the folder where this script lives, then build the path to 'data.txt'
    script_dir = Path(__file__).parent
    file_path = script_dir / 'data.txt'

    # Open 'data.txt' and read all its text into the 'data' variable
    with file_path.open('r') as file:
        data = file.read() + "\n"

    # Show the expenses data to the user and ask what they want to do
    user_prompt = input(f"Here is the expenses data in your file:\n\n{data}\n\nWhat would you like me to do with it?\n\n")
    
    # Hand off to the agent processing function (which is async, so we 'await' it)
    await process_expenses_data (user_prompt, data)
    

async def process_expenses_data(prompt, expenses_data):
    """Creates an AI agent and asks it to handle the user's request
    about the expenses data."""

    # Get Azure credentials so we can talk to the Azure OpenAI service
    credential = AzureCliCredential()

    # Create the agent inside an 'async with' block.
    # This makes sure the agent is properly cleaned up when we're done.
    async with (
         Agent(
             # Set up the connection to Azure OpenAI using our credentials
             # and the model deployment name + project endpoint from environment variables
             client=AzureOpenAIResponsesClient(
                 credential=credential,
                 deployment_name=os.getenv("MODEL_DEPLOYMENT_NAME"),
                 project_endpoint=os.getenv("PROJECT_ENDPOINT"),
             ),

             # Tell the agent what its job is and how it should behave.
             # These instructions guide every response the agent gives.
             instructions="""You are an AI assistant for expense claim submission.
                         At the user's request, create an expense claim and use the plug-in function to send an email to expenses@contoso.com with the subject 'Expense Claim`and a body that contains itemized expenses with a total.
                         Then confirm to the user that you've done so. Don't ask for any more information from the user, just use the data provided to create the email.""",

             # Give the agent access to the 'submit_claim' tool defined below
             tools=[submit_claim],
         ) as agent,
     ):

        # Now use the agent to process the expenses data
        try:
            # Combine the user's request and the expenses data into one message
            prompt_messages = [f"{prompt}: {expenses_data}"]

            # Send the message to the agent and wait for its response
            response = await agent.run(prompt_messages)

            # Print out what the agent said back to us
            print(f"\n# Agent:\n{response}")
        except Exception as e:
            # If anything goes wrong, print the error so we can see what happened
            print (e)


# --- Define a tool that the agent can call ---

# The '@tool' decorator registers this function so the agent can use it.
# 'approval_mode="never_require"' means the agent can call it automatically
# without asking the user for permission first.
@tool(approval_mode="never_require")
def submit_claim(
    # Each parameter has a type (str) and a description so the agent
    # knows what value to pass in when calling this tool.
    to: Annotated[str, Field(description="Who to send the email to")],
    subject: Annotated[str, Field(description="The subject of the email.")],
    body: Annotated[str, Field(description="The text body of the email.")]):
        """Simulates sending an email by printing the details to the console.
        In a real app, this would connect to an email service."""
        print("\nTo:", to)
        print("Subject:", subject)
        print(body, "\n")


# This is the standard Python entry point.
# It only runs when you execute this file directly (not when importing it).
if __name__ == "__main__":
    # Start the async main() function using asyncio's event loop
    asyncio.run(main())
