#!/usr/bin/env python3
import asyncio
import sys
import os
sys.path.append('.')
from whatsapp import get_all_contacts_optimized

async def main():
    contacts = await get_all_contacts_optimized()
    print(contacts)

if __name__ == "__main__":
    asyncio.run(main()) 