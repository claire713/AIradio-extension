import asyncio
from supervisor import Supervisor
import yaml

async def main():
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    supervisor = Supervisor(config)
    await supervisor.run()

if __name__ == "__main__":
    asyncio.run(main())
