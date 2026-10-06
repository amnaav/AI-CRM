from activity_logs.services import get_client_ip


class ActivityLoggingMiddleware:
    """
    Lightweight middleware to capture client IP on the request object.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.client_ip = get_client_ip(request)
        response = self.get_response(request)
        return response
