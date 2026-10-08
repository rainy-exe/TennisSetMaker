class ApiError(Exception):
    def __init__(self, message, status_code=400, code="bad_request", details=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.details = details

    def to_dict(self):
        payload = {"error": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload
