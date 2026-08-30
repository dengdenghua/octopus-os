class RetryPolicy:
    def should_retry(self, error: str, attempt: int) -> bool:
        return attempt < 3
