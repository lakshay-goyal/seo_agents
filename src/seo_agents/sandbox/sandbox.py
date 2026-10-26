from langsmith.sandbox import SandboxClient


def create_sandbox():
    """Create and run the LangChain (LangSmith) sandbox, return it."""
    print("Creating sandbox...")
    client = SandboxClient()
    sandbox = client.create_sandbox()
    sandbox.run("python3 -c 'print(2 + 2)'")
    print("Sandbox is running.")
    return sandbox
