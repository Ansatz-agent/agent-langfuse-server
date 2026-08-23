from django.http import JsonResponse


def lockout_response(request, credentials=None, *args, **kwargs):
    return JsonResponse({"detail": "Too many login attempts. Try again later."}, status=429)
