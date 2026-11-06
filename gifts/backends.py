from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    # LoginView/AuthenticationForm pass the identifier as `username`.
    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        email = username if username is not None else kwargs.get(user_model.USERNAME_FIELD)
        try:
            user = user_model.objects.get(**{f"{user_model.USERNAME_FIELD}__iexact": email})
        except user_model.DoesNotExist:
            return None
        else:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
