import re
from pathlib import Path

def normalize_whitespace(text: str) -> str:
    # Remove all leading/trailing whitespace and normalize internal whitespace
    return re.sub(r'\s+', ' ', text).strip()

def test_typescript_contract():
    # Path to authoritative contract
    contract_path = Path("../contracts/CONTRACT.md")
    assert contract_path.exists(), "CONTRACT.md not found"
    
    with open(contract_path, "r", encoding="utf-8") as f:
        contract_content = f.read()
        
    # Extract the code block from Section 9.4
    match = re.search(r'### 9\.4 Props contract.*?```ts\n(.*?)```', contract_content, re.DOTALL)
    assert match is not None, "Could not find Section 9.4 code block in CONTRACT.md"
    authoritative_ts = match.group(1)
    
    # Path to implementation
    impl_path = Path("dashboard/src/contract/types.ts")
    assert impl_path.exists(), "types.ts not found"
    
    with open(impl_path, "r", encoding="utf-8") as f:
        impl_ts = f.read()
        
    assert normalize_whitespace(authoritative_ts) == normalize_whitespace(impl_ts)
