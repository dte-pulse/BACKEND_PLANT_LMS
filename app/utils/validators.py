def validate_file_type(file_name: str) -> bool:
    return file_name.lower().endswith((".pdf", ".docx"))
