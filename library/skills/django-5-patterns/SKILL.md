---
name: django-5-patterns
description: Django 5.x and Django REST Framework modern best practices, patterns, and production-ready implementations for API development with Type Hints, DRF, Authentication, Permissions, and Testing.
user-invokable: true
---

# Django 5.x + Django REST Framework - Modern API Development

**Wersja:** 5.0+  
**Framework:** Django REST Framework 3.14+  
**Python:** 3.10+  
**Status:** Production-Ready

## Spis treści

1. Project Structure
2. Model Design
3. Serializers
4. ViewSets & APIViews
5. Authentication
6. Permissions
7. Signals
8. Testing
9. Performance
10. Complete Examples

---

## Project Structure

Nowoczesna organizacja projektów Django powinna być skalowalna, czytelna i łatwa w utrzymaniu.

```
django_project/
├── manage.py
├── pyproject.toml           # Nowa metoda konfiguracji (PEP 621)
├── django.ini
├── config/
│   ├── __init__.py
│   ├── settings/
│   │   ├── base.py          # Wspólne ustawienia
│   │   ├── development.py   # Dev environment
│   │   ├── production.py    # Production environment
│   │   └── testing.py       # Test configuration
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── apps/
│   ├── __init__.py
│   ├── users/
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── viewsets.py
│   │   ├── permissions.py
│   │   ├── signals.py
│   │   ├── urls.py
│   │   ├── managers.py
│   │   ├── services.py      # Business logic
│   │   ├── filters.py
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   ├── test_models.py
│   │   │   ├── test_views.py
│   │   │   ├── test_serializers.py
│   │   │   ├── conftest.py  # pytest fixtures
│   │   │   └── factories.py # Factory Boy factories
│   │   └── admin.py
│   └── blog/
│       └── ... (similar structure)
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Shared fixtures
│   └── factories/
├── static/
├── media/
└── requirements/
    ├── base.txt
    ├── development.txt
    └── production.txt
```

**Best Practices:**
- Każda aplikacja powinna być samodowolna (self-contained)
- Umieszczaj business logic w `services.py`
- Używaj `managers.py` dla custom querysetów
- Separuj testy w dedykowanym folderze z `conftest.py`

---

## Model Design - Zaawansowane

### Base Abstract Model

```python
# apps/core/models.py
from django.db import models
from django.utils import timezone
from typing import Optional
from django.db.models import Model, Manager, QuerySet

class TimestampedModel(models.Model):
    """Abstract model z audit fields"""
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Czas utworzenia obiektu"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Czas ostatniej aktualizacji"
    )
    
    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['updated_at']),
        ]

class SoftDeleteModel(models.Model):
    """Soft delete pattern - bezpieczniejszy niż hard delete"""
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        db_index=True
    )
    
    class Meta:
        abstract = True
    
    def soft_delete(self) -> None:
        """Oznacz obiekt jako usunięty"""
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])
    
    def restore(self) -> None:
        """Przywróć usunięty obiekt"""
        self.deleted_at = None
        self.save(update_fields=['deleted_at'])
```

### Custom QuerySet i Manager

```python
# apps/users/managers.py
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db.models import QuerySet, Q
from typing import Optional

class UserQuerySet(QuerySet):
    """Custom queryset z utility methods"""
    
    def active(self) -> QuerySet:
        """Filtruj tylko aktywnych użytkowników"""
        return self.filter(is_active=True)
    
    def by_email(self, email: str) -> QuerySet:
        """Case-insensitive szukanie po email"""
        return self.filter(email__iexact=email)
    
    def with_profile(self) -> QuerySet:
        """Prefetch related profile"""
        return self.select_related('profile')

class UserManager(DjangoUserManager.from_queryset(UserQuerySet)):
    """Custom manager łączący Django UserManager z custom QuerySet"""
    
    def create_user(
        self,
        email: str,
        password: Optional[str] = None,
        **extra_fields
    ):
        """Create regular user"""
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)
    
    def create_superuser(
        self,
        email: str,
        password: str,
        **extra_fields
    ):
        """Create superuser"""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self._create_user(email, password, **extra_fields)
```

### Advanced User Model

```python
# apps/users/models.py
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _
from datetime import datetime, timedelta
from typing import Optional
from .managers import UserManager

class User(AbstractBaseUser, PermissionsMixin, TimestampedModel):
    """Custom user model - zawsze preferuj nad domyślnym User"""
    
    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrator")
        MODERATOR = "moderator", _("Moderator")
        USER = "user", _("Użytkownik")
    
    email = models.EmailField(
        unique=True,
        db_index=True,
        validators=[URLValidator()]
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER
    )
    
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    
    last_login = models.DateTimeField(null=True, blank=True)
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    
    # Custom fields
    avatar = models.ImageField(
        upload_to='avatars/%Y/%m/',
        null=True,
        blank=True
    )
    bio = models.TextField(blank=True, max_length=500)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        db_table = 'auth_user'
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['is_active']),
            models.Index(fields=['role']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(email__contains='@'),
                name='email_contains_at'
            ),
        ]
    
    def __str__(self) -> str:
        return f"{self.email} ({self.get_role_display()})"
    
    @property
    def full_name(self) -> str:
        """Full name of user"""
        return f"{self.first_name} {self.last_name}".strip()
    
    def verify_email(self) -> None:
        """Mark email as verified"""
        self.email_verified = True
        self.email_verified_at = timezone.now()
        self.save(update_fields=['email_verified', 'email_verified_at'])

class UserProfile(TimestampedModel, SoftDeleteModel):
    """Extended user profile"""
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    
    timezone = models.CharField(
        max_length=63,
        default='UTC'
    )
    notifications_enabled = models.BooleanField(default=True)
    newsletter_subscribed = models.BooleanField(default=False)
    
    class Meta:
        verbose_name = _("User Profile")
        indexes = [
            models.Index(fields=['user']),
        ]
    
    def __str__(self) -> str:
        return f"Profile of {self.user.email}"
```

---

## Serializers - Walidacja i Nested Objects

### Base Serializer mixin

```python
# apps/core/serializers.py
from rest_framework import serializers
from typing import Any, Dict
from django.utils.translation import gettext_lazy as _

class TimestampedSerializerMixin:
    """Mixin dla serializers z timestamp fields"""
    
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

class BaseSerializer(TimestampedSerializerMixin, serializers.ModelSerializer):
    """Base serializer z common utilities"""
    
    def validate_email(self, value: str) -> str:
        """Case-insensitive email validation"""
        return value.lower().strip()
    
    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Override dla cross-field validation"""
        return data
```

### User Serializers

```python
# apps/users/serializers.py
from rest_framework import serializers
from typing import Optional, Dict, Any
from .models import User, UserProfile
from ..core.serializers import BaseSerializer
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

class UserProfileSerializer(BaseSerializer):
    """Serializer dla UserProfile"""
    
    class Meta:
        model = UserProfile
        fields = [
            'id', 'timezone', 'notifications_enabled',
            'newsletter_subscribed', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class UserDetailSerializer(BaseSerializer):
    """Full user serializer z nested profile"""
    
    profile = UserProfileSerializer(read_only=True)
    full_name = serializers.CharField(read_only=True)
    
    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'role', 'is_active', 'email_verified', 'avatar', 'bio',
            'profile', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'email_verified'
        ]

class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer dla tworzenia użytkowników"""
    
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        help_text="Password musi nie być krótszy niż 8 znaków"
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True
    )
    
    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name',
            'password', 'password_confirm'
        ]
    
    def validate_password(self, value: str) -> str:
        """Validate password against Django validators"""
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value
    
    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Cross-field validation"""
        if data['password'] != data.pop('password_confirm'):
            raise serializers.ValidationError({
                'password': _("Passwords do not match")
            })
        return data
    
    def create(self, validated_data: Dict[str, Any]) -> User:
        """Create user i set password hashable"""
        user = User.objects.create_user(**validated_data)
        # Automatycznie tworzony profil przez signal
        return user

class UserUpdateSerializer(BaseSerializer):
    """Serializer dla aktualizacji użytkownika"""
    
    password = serializers.CharField(
        write_only=True,
        required=False,
        min_length=8
    )
    
    class Meta:
        model = User
        fields = [
            'email', 'first_name', 'last_name',
            'password', 'avatar', 'bio'
        ]
    
    def validate_password(self, value: Optional[str]) -> Optional[str]:
        """Validate if provided"""
        if value:
            try:
                validate_password(value)
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.messages)
        return value
    
    def update(
        self,
        instance: User,
        validated_data: Dict[str, Any]
    ) -> User:
        """Handle password update separately"""
        password = validated_data.pop('password', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        if password:
            instance.set_password(password)
        
        instance.save()
        return instance
```

---

## ViewSets & APIViews - CRUD Operacje

### Custom ViewSet Base Class

```python
# apps/core/viewsets.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.request import Request
from typing import Any, Dict, Optional
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page

class BaseViewSet(viewsets.ModelViewSet):
    """Base ViewSet z common utilities"""
    
    pagination_class = None
    filter_backends = []
    search_fields = []
    ordering_fields = []
    ordering = []
    
    def get_serializer_class(self):
        """Override dla różnych serializers na podstawie action"""
        if self.action == 'create':
            return getattr(self, 'create_serializer_class', self.serializer_class)
        elif self.action == 'update':
            return getattr(self, 'update_serializer_class', self.serializer_class)
        elif self.action == 'partial_update':
            return getattr(self, 'update_serializer_class', self.serializer_class)
        return self.serializer_class
    
    def get_queryset(self):
        """Override dla custom filtering"""
        return super().get_queryset()
    
    @action(detail=False, methods=['get'])
    def me(self, request: Request) -> Response:
        """Get current user data"""
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
```

### User ViewSet Implementation

```python
# apps/users/viewsets.py
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.permissions import IsAuthenticated, AllowAny
from django_filters.rest_framework import DjangoFilterBackend
from typing import Any
from .models import User
from .serializers import (
    UserDetailSerializer,
    UserCreateSerializer,
    UserUpdateSerializer
)
from .permissions import IsOwnerOrReadOnly
from .filters import UserFilter
from ..core.viewsets import BaseViewSet

class UserViewSet(BaseViewSet):
    """ViewSet dla user management"""
    
    queryset = User.objects.select_related('profile').all()
    serializer_class = UserDetailSerializer
    create_serializer_class = UserCreateSerializer
    update_serializer_class = UserUpdateSerializer
    
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = UserFilter
    search_fields = ['email', 'first_name', 'last_name']
    ordering_fields = ['created_at', 'email', 'role']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Filter based on permissions"""
        if self.request.user.is_staff:
            return super().get_queryset()
        return User.objects.filter(id=self.request.user.id)
    
    def get_permissions(self):
        """Dynamic permissions"""
        if self.action == 'create':
            return [AllowAny()]
        elif self.action in ['update', 'partial_update', 'destroy']:
            return [IsOwnerOrReadOnly()]
        return [IsAuthenticated()]
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def verify_email(self, request: Request, pk: Any = None) -> Response:
        """Verify user email"""
        user = self.get_object()
        if request.user.id != user.id and not request.user.is_staff:
            return Response(
                {'detail': 'You can only verify your own email'},
                status=status.HTTP_403_FORBIDDEN
            )
        user.verify_email()
        return Response({'status': 'email verified'})
    
    @action(detail=True, methods=['post'])
    def set_password(self, request: Request, pk: Any = None) -> Response:
        """Set new password"""
        user = self.get_object()
        serializer = UserUpdateSerializer(
            user,
            data={'password': request.data.get('password')},
            partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response({'status': 'password changed'})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
```

---

## Authentication - Token i JWT

### Token Authentication Setup

```python
# settings/base.py
INSTALLED_APPS = [
    # ...
    'rest_framework',
    'rest_framework.authtoken',
]

# apps/users/serializers.py
class TokenSerializer(serializers.Serializer):
    """Serializer dla login endpoint"""
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    token = serializers.CharField(read_only=True)
    
    def validate(self, data):
        from django.contrib.auth import authenticate
        from rest_framework.authtoken.models import Token
        user = authenticate(
            username=data['email'],
            password=data['password']
        )
        if not user:
            raise serializers.ValidationError("Invalid credentials")
        token, created = Token.objects.get_or_create(user=user)
        data['token'] = token.key
        return data
```

### JWT Authentication (Production-Ready)

```python
# settings/base.py
INSTALLED_APPS = [
    # ...
    'rest_framework',
    'rest_framework_simplejwt',
]

from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
}

# apps/users/views.py
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Extended JWT serializer with user data"""
    
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['email'] = user.email
        token['role'] = user.role
        return token

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

# config/urls.py
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('api/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
```

---

## Permissions - Object-Level i Custom

### Custom Permission Classes

```python
# apps/users/permissions.py
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.views import APIView
from typing import Any

class IsOwnerOrReadOnly(permissions.BasePermission):
    """Allow owner to edit their own objects"""
    
    def has_object_permission(
        self,
        request: Request,
        view: APIView,
        obj: Any
    ) -> bool:
        # Read permissions for any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only for owner
        return obj.user == request.user

class IsAdminOrReadOnly(permissions.BasePermission):
    """Only admin can write, others can read"""
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_staff

class HasRequiredRole(permissions.BasePermission):
    """Check if user has required role"""
    
    required_roles = []
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role in self.required_roles

class IsVerifiedEmail(permissions.BasePermission):
    """User must have verified email"""
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        return (
            request.user and
            request.user.is_authenticated and
            request.user.email_verified
        )
```

---

## Signals - Proper Usage i Performance

### Best Practices

```python
# apps/users/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.mail import send_mail
from django.conf import settings
from celery import shared_task
import logging
from typing import Type, Any

logger = logging.getLogger(__name__)

# ✅ GOOD: Async task via Celery
@shared_task
def send_welcome_email(user_id: int) -> None:
    """Send welcome email asynchronously"""
    from .models import User
    try:
        user = User.objects.get(id=user_id)
        send_mail(
            subject='Welcome!',
            message=f'Hello {user.full_name}',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except User.DoesNotExist:
        logger.warning(f"User with id {user_id} not found")

@receiver(post_save, sender=User)
def create_user_profile(
    sender: Type,
    instance,
    created: bool,
    **kwargs: Any
) -> None:
    """Create UserProfile when User is created"""
    from .models import UserProfile
    
    if created:
        UserProfile.objects.create(user=instance)
        send_welcome_email.delay(instance.id)

@receiver(post_save, sender=User)
def update_user_cache(
    sender: Type,
    instance,
    created: bool,
    **kwargs: Any
) -> None:
    """Invalidate cache when user changes"""
    from django.core.cache import cache
    cache.delete(f"user_{instance.id}")

# apps/apps.py - Register signals
from django.apps import AppConfig

class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.users'
    
    def ready(self) -> None:
        import apps.users.signals  # noqa
```

---

## Testing - APITestCase i Factories

### Fixtures i Setup

```python
# apps/users/tests/conftest.py
import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()

@pytest.fixture
def api_client() -> APIClient:
    """Provide API client"""
    return APIClient()

@pytest.fixture
def authenticated_client(api_client: APIClient, user) -> APIClient:
    """Provide authenticated API client"""
    api_client.force_authenticate(user=user)
    return api_client

@pytest.fixture
def user():
    """Create test user"""
    return User.objects.create_user(
        email='test@example.com',
        password='<hasło>',
        first_name='Test',
        last_name='User'
    )

@pytest.fixture
def admin_user():
    """Create admin user"""
    return User.objects.create_superuser(
        email='admin@example.com',
        password='<hasło>',
        first_name='Admin',
        last_name='User'
    )
```

### Factory Boy

```python
# apps/users/tests/factories.py
import factory
from django.contrib.auth import get_user_model

User = get_user_model()

class UserFactory(factory.django.DjangoModelFactory):
    """Factory dla User"""
    
    class Meta:
        model = User
    
    email = factory.Faker('email')
    first_name = factory.Faker('first_name')
    last_name = factory.Faker('last_name')
    role = User.Role.USER
    is_active = True
    email_verified = True
    
    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        raw_password = extracted or '<hasło>'
        obj.set_password(raw_password)
        if create:
            obj.save()

class AdminUserFactory(UserFactory):
    """Factory dla admin user"""
    role = User.Role.ADMIN
    is_staff = True
```

### API Tests

```python
# apps/users/tests/test_views.py
import pytest
from rest_framework import status
from django.contrib.auth import get_user_model

User = get_user_model()
pytestmark = pytest.mark.django_db

class TestUserViewSet:
    """Tests dla User ViewSet"""
    
    def test_create_user(self, api_client):
        """Create new user"""
        data = {
            'email': 'newuser@example.com',
            'password': '<hasło>',
            'password_confirm': '<hasło>',
            'first_name': 'New',
            'last_name': 'User'
        }
        response = api_client.post('/api/users/', data)
        assert response.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(email='newuser@example.com').exists()
    
    def test_update_own_profile(self, authenticated_client, user):
        """User can update own profile"""
        data = {'bio': 'Updated bio'}
        response = authenticated_client.patch(f'/api/users/{user.id}/', data)
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.bio == 'Updated bio'
    
    def test_cannot_update_other_user(self, authenticated_client, user):
        """User cannot update other users"""
        other_user = User.objects.create_user(
            email='other@example.com',
            password='<hasło>'
        )
        response = authenticated_client.patch(
            f'/api/users/{other_user.id}/',
            {'bio': 'Hacked'}
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
```

---

## Performance - Optimization Techniques

### Select/Prefetch Related

```python
# ❌ BAD: N+1 queries
posts = Post.objects.all()
for post in posts:
    print(post.author.email)  # Query per iteration!

# ✅ GOOD: Single query
posts = Post.objects.select_related('author')
for post in posts:
    print(post.author.email)  # No additional queries

# ✅ GOOD: Prefetch for reverse relations
posts = Post.objects.prefetch_related('comments__author')
for post in posts:
    for comment in post.comments.all():
        print(comment.author.email)  # Optimized!

# apps/blog/viewsets.py
from django.db.models import Prefetch

class PostViewSet(viewsets.ModelViewSet):
    """Post ViewSet z optimized queries"""
    
    queryset = Post.objects.select_related('author').prefetch_related(
        Prefetch('comments', queryset=Comment.objects.select_related('author'))
    )
```

### Caching Strategies

```python
# apps/core/cache.py
from django.core.cache import cache
from functools import wraps
from typing import Callable, Any
import hashlib

def cache_api_response(timeout: int = 300):
    """Decorator dla caching API responses"""
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapper(request, *args, **kwargs) -> Any:
            cache_key = f"api:{request.path}:{request.GET.dict()}"
            cache_key = hashlib.md5(cache_key.encode()).hexdigest()
            
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            
            response = view_func(request, *args, **kwargs)
            if response.status_code == 200:
                cache.set(cache_key, response, timeout)
            return response
        return wrapper
    return decorator
```

### Database Indexes

```python
# apps/blog/models.py
class Post(models.Model):
    title = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(unique=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['author', 'created_at']),
            models.Index(
                fields=['is_published', 'created_at'],
                condition=models.Q(is_published=True),
                name='published_posts_idx'
            ),
        ]
```

---

## Complete Examples

### 1. User Management API

```python
# config/urls.py
from rest_framework.routers import DefaultRouter
from apps.users.viewsets import UserViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet)

urlpatterns = [
    path('api/', include(router.urls)),
    path('api/token/', CustomTokenObtainPairView.as_view()),
    path('api/token/refresh/', TokenRefreshView.as_view()),
]
```

### 2. Blog API - Nested Serializers

```python
# apps/blog/models.py
class Post(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    content = models.TextField()
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    created_at = models.DateTimeField(auto_now_add=True)
    is_published = models.BooleanField(default=False)

class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

# apps/blog/serializers.py
class CommentSerializer(serializers.ModelSerializer):
    author = UserDetailSerializer(read_only=True)
    
    class Meta:
        model = Comment
        fields = ['id', 'content', 'author', 'created_at']

class PostDetailSerializer(serializers.ModelSerializer):
    author = UserDetailSerializer(read_only=True)
    comments = CommentSerializer(many=True, read_only=True)
    
    class Meta:
        model = Post
        fields = ['id', 'title', 'slug', 'content', 'author', 'comments']
```

### 3. Permission-Based Access Control

```python
class PostViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthorOrReadOnly]
    
    def perform_create(self, serializer):
        serializer.save(author=self.request.user)
```

### 4. Advanced Filtering & Search

```python
# apps/blog/filters.py
from django_filters import FilterSet, CharFilter

class PostFilter(FilterSet):
    title = CharFilter(
        field_name='title',
        lookup_expr='icontains'
    )
    author_email = CharFilter(
        field_name='author__email',
        lookup_expr='iexact'
    )
    
    class Meta:
        model = Post
        fields = ['title', 'author', 'is_published']

# apps/blog/viewsets.py
class PostViewSet(viewsets.ModelViewSet):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_class = PostFilter
    search_fields = ['title', 'content']
    ordering_fields = ['created_at', 'title']
    ordering = ['-created_at']
```

---

## Configuration Best Practices

```python
# settings/base.py
from pathlib import Path
from datetime import timedelta
from environs import Env

env = Env()
BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost'])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'rest_framework',
    'rest_framework_simplejwt',
    'django_filters',
    'corsheaders',
    'drf_spectacular',
    'apps.users.apps.UsersConfig',
    'apps.blog.apps.BlogConfig',
]

DATABASES = {
    'default': env.dbtuple(
        'DATABASE_URL',
        default='sqlite:///db.sqlite3'
    )
}

CACHES = {
    'default': env.cache_url(
        'CACHE_URL',
        default='locmem://'
    )
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
}
```

---

> Pliki `references/org-*.md`, jeśli są, to rozszerzenia warstwy organizacji: przeczytaj te, które pasują do projektu.

---

## Role Enrichment & Nested Data in Serializers

**Problem**: API endpoints returning related objects often require N+1 queries:
- Frontend calls `GET /api/users/` → returns users + roles (strings)
- Frontend then calls `GET /api/player-disciplines/?player={id}` for each user
- Result: 1 + N HTTP requests for N users

**Solution**: Use `to_representation()` to nest enriched data directly in response, eliminating N+1 at API level.

### Pattern: Role-Specific Discipline Enrichment

```python
# accounts/api/serializers/user_management.py
from rest_framework import serializers
from typing import Dict, Any, List, Optional
from django.contrib.auth.models import Group
from rest_framework.utils.serializer_helpers import ReturnDict

class RoleWithDisciplines(serializers.Serializer):
    """Nested role structure with disciplines"""
    name = serializers.CharField()
    disciplines = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list
    )

class UserManagementSerializer(serializers.ModelSerializer):
    """User serializer that nests disciplines per role"""
    
    roles = RoleWithDisciplines(many=True, read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'first_name', 'last_name', 'email', 'roles']
    
    def to_representation(self, instance: User) -> Dict[str, Any]:
        """Convert user to JSON with role-specific disciplinies nested"""
        data = super().to_representation(instance)
        
        # Build roles list with disciplines
        roles_data: List[Dict[str, Any]] = []
        user_groups = instance.groups.all()
        
        for group in user_groups:
            role_dict: Dict[str, Any] = {
                'name': group.name,
                'disciplines': []
            }
            
            # Role-specific discipline extraction
            if group.name == 'PLAYER':
                # PLAYER: disciplines via PlayerDiscipline (M2M through intermediate)
                if hasattr(instance, 'player_profile') and instance.player_profile:
                    for pd in instance.player_profile.player_disciplines.all():
                        role_dict['disciplines'].append({
                            'id': pd.discipline.id,
                            'name': pd.discipline.name,
                            'category': pd.discipline.category,
                            'is_individual': pd.discipline.is_individual,
                        })
            
            elif group.name == 'COACH':
                # COACH: direct M2M (coach_profile.disciplines)
                if hasattr(instance, 'coach_profile') and instance.coach_profile:
                    for discipline in instance.coach_profile.disciplines.all():
                        role_dict['disciplines'].append({
                            'id': discipline.id,
                            'name': discipline.name,
                            'category': discipline.category,
                            'is_individual': discipline.is_individual,
                        })
            
            # ADMIN, PARENT, TEAM_MANAGER: no disciplines
            
            roles_data.append(role_dict)
        
        data['roles'] = roles_data
        return data
```

### Query Optimization: Prefetch Strategy

Use `Prefetch` objects in ViewSet for mixed M2M relationships:

```python
# accounts/api/views/user_management.py
from django.db.models import Prefetch, Q
from rest_framework import viewsets
from typing import QuerySet
from .serializers import UserManagementSerializer
from core.models import PlayerDiscipline
from accounts.models import User

class UserManagementViewSet(viewsets.ModelViewSet):
    serializer_class = UserManagementSerializer
    
    def get_queryset(self) -> QuerySet:
        """Optimize queries for nested role/discipline data"""
        
        # For PLAYER roles: Prefetch through intermediate model
        player_disciplines = PlayerDiscipline.objects.select_related(
            'discipline', 'training_group'
        )
        
        return User.objects.prefetch_related(
            # PLAYER disciplines via intermediate model
            Prefetch(
                'player_profile__player_disciplines',
                queryset=player_disciplines
            ),
            # COACH disciplines via direct M2M
            'coach_profile__disciplines',
            # User groups
            'groups'
        ).select_related(
            'player_profile',
            'coach_profile'
        )
```

### Key Points

**Nested Response Format**:
```json
{
  "id": 50,
  "first_name": "Monika",
    "email": "monika@example.com",
  "roles": [
    {
      "name": "COACH",
      "disciplines": [
        {"id": 4, "name": "Akrobatyka", "category": "Gimnastyka", "is_individual": true},
        {"id": 3, "name": "Siatkówka", "category": "", "is_individual": false}
      ]
    },
    {"name": "PLAYER", "disciplines": []},
    {"name": "ADMIN", "disciplines": []}
  ]
}
```

**Benefits**:
- ✅ Eliminates N+1 API pattern: 1 request instead of 1+N
- ✅ Role-specific extraction handles different M2M patterns (through vs direct)
- ✅ Terminal prefetch avoids multiple queries for each role
- ✅ Frontend gets all discipline data in single response

**Caveats**:
- ⚠️ Be careful with model architecture (direct M2M vs through intermediate)
- ⚠️ Different role types may have disciplines in different relations
- ⚠️ Update `Prefetch` when model relationships change
- ⚠️ Use `select_related()` for FK, `prefetch_related()` for M2M

---

## Summary - Key Takeaways

1. **Type Hints** - `def get_user(user_id: int) -> User:`
2. **Custom Managers** - Encapsulate query logic
3. **Select/Prefetch** - Avoid N+1 queries
4. **Test Everything** - pytest + Factory Boy
5. **Cache Strategically** - Don't cache unnecessarily
6. **Permissions** - Class and object-level
7. **Async Tasks** - Use Celery for long operations
8. **Validate Early** - Custom serializer validators
9. **Document API** - Use drf-spectacular
10. **Monitor Performance** - Use django-silk
11. **Signals** - Remember auto-create on m2m_changed; update don't create again!

Django 5.x + DRF to potężna kombinacja dla budowy skalowalnych, production-ready APIs. Pamiętaj o best practices, testach i optimizacjach od samego początku!

---

## Filtrowanie po rolach (Groups) bez django-filter

Projekt **nie ma zainstalowanego django-filter**. Filtrowanie implementujemy ręcznie w `get_queryset()`.

### Pattern: filtr roli przez grupy Django + filtr statusu

```python
from typing import Dict

class UserManagementViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        user = self.request.user
        if not user.groups.filter(name__in=['Admins', 'Super Admins']).exists():
            return User.objects.none()

        qs = User.objects.all().order_by('-created_at')

        # Filtr po roli → przez grupy Django
        role_param = self.request.query_params.get('role')
        if role_param:
            role_to_group: Dict[str, str] = {
                'PLAYER': 'Players', 'COACH': 'Coaches',
                'PARENT': 'Parents', 'TEAM_MANAGER': 'Team Managers',
                'ADMIN': 'Admins', 'SUPER_ADMIN': 'Super Admins',
            }
            group_name = role_to_group.get(role_param.upper())
            if group_name:
                qs = qs.filter(groups__name=group_name)

        # Filtr po statusie → bezpośrednio po polu modelu
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        return qs
```

**Uwaga**: `filterset_fields` i `ordering_fields` w klasie ViewSet NIE zastępują ręcznego `get_queryset()`.
Jeśli oba są zdefiniowane, ręczny `get_queryset()` ma priorytet.

### Pattern: action endpoints dla zmiany statusu

```python
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

class UserManagementViewSet(viewsets.ModelViewSet):

    @action(detail=True, methods=['post'])
    def activate(self, request: Request, pk=None) -> Response:
        user = self.get_object()
        user.status = UserStatus.ACTIVE
        user.save()
        return Response({'detail': f'User {user.email} activated'})

    @action(detail=True, methods=['post'])
    def deactivate(self, request: Request, pk=None) -> Response:
        user = self.get_object()
        user.status = UserStatus.DEACTIVATED
        user.save()
        return Response({'detail': f'User {user.email} deactivated'})

    @action(detail=True, methods=['post'])
    def suspend(self, request: Request, pk=None) -> Response:
        user = self.get_object()
        user.status = UserStatus.SUSPENDED
        user.save()
        return Response({'detail': f'User {user.email} suspended'})
```

Generowane URL-e: `POST /api/users/{id}/activate/`, `.../deactivate/`, `.../suspend/`

### Filtrowanie po `training_group=none` (nullable FK)

```python
def get_queryset(self):
    qs = super().get_queryset()
    training_group_param = self.request.query_params.get("training_group")
    if training_group_param == "none":
        qs = qs.filter(training_group__isnull=True)   # brak grupy
    elif training_group_param is not None:
        qs = qs.filter(training_group_id=training_group_param)
    return qs
```

Wywołanie: `GET /api/player-disciplines/?training_group=none` → tylko zawodnicy bez grupy.

---

### Multi-field Search Filtering

**Pattern**: Wyszukiwanie textowe across multiple fields - key pattern for user management lists.

```python
# accounts/api/views/user_management.py
from django.db.models import Q

class UserManagementViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Text search by name/email/phone
        search_param = self.request.query_params.get('search')
        if search_param:
            query = search_param.strip()
            if query:
                qs = qs.filter(
                    Q(first_name__icontains=query)
                    | Q(last_name__icontains=query)
                    | Q(email__icontains=query)
                    | Q(phone_number__icontains=query)
                )
        
        return qs.distinct()  # IMPORTANT: .distinct() prevents M2M duplicates
```

**Frontend Usage:**
```typescript
// Frontend sends: GET /api/users/?search=coach
// Backend returns: Users matching "coach" in any of 4 fields
```

**Best Practices:**
- ✅ Use `Q()` objects for OR logic queries
- ✅ Apply `.distinct()` when filtering returns duplicates (M2M relationships)
- ✅ Strip whitespace: `query = search_param.strip()`
- ✅ Use `__icontains` for case-insensitive search
- ❌ Don't use `__contains` (case-sensitive, slower)
- ❌ Don't skip `.distinct()` - causes duplicate rows from JOINs

---

### Many-to-Many Queryset Filtering

**Pattern**: Filter through M2M relationships using double underscore syntax.

```python
# accounts/api/views/user_management.py
from django.db.models import Q

class UserManagementViewSet(viewsets.ModelViewSet):
    queryset = User.objects.prefetch_related(
        'player_profile__player_disciplines',
        'coach_profile__disciplines'
    )
    
    def get_queryset(self):
        qs = super().get_queryset()
        
        # Filter users assigned to specific discipline (coaches or players)
        discipline_param = self.request.query_params.get('discipline')
        if discipline_param:
            try:
                discipline_id = int(discipline_param)
            except (TypeError, ValueError):
                return User.objects.none()
            
            qs = qs.filter(
                Q(player_profile__player_disciplines__discipline_id=discipline_id)
                | Q(coach_profile__disciplines__id=discipline_id)
            )
        
        return qs.distinct()
```

**Breakdown:**
- `player_profile__player_disciplines__discipline_id`: Access through Player → PlayerDiscipline M2M → Discipline
- `coach_profile__disciplines__id`: Access through Coach → Discipline M2M directly
- `Q()` allows convenient OR between two separate M2M paths
- `.distinct()` prevents duplicate rows when same user matches multiple M2M relations

**Alternative with Prefetch (for even better performance):**
```python
from django.db.models import Prefetch

def get_queryset(self):
    qs = super().get_queryset()
    
    discipline_param = self.request.query_params.get('discipline')
    if discipline_param:
        discipline_id = int(discipline_param)
        
        # Prefetch only disciplines matching the filter
        player_prefetch = Prefetch(
            'player_profile__player_disciplines',
            PlayerDiscipline.objects.filter(discipline_id=discipline_id)
        )
        coach_prefetch = Prefetch(
            'coach_profile__disciplines',
            Discipline.objects.filter(id=discipline_id)
        )
        
        qs = (qs
            .filter(
                Q(player_profile__player_disciplines__discipline_id=discipline_id)
                | Q(coach_profile__disciplines__id=discipline_id)
            )
            .prefetch_related(player_prefetch, coach_prefetch)
        )
    
    return qs.distinct()
```

**Best Practices:**
- ✅ Always use `.prefetch_related()` for M2M to avoid N+1 queries
- ✅ Apply `.distinct()` when filtering M2M (prevents JOINs from multiplying rows)
- ✅ Use double underscore to traverse relationships: `model__relation__field`
- ✅ Consider `Prefetch` for advanced filtering within related querysets
- ❌ Don't use `.select_related()` for M2M (use `.prefetch_related()`)
- ❌ Don't forget `.distinct()` - causes incorrect pagination and counts

**Testing:**
```python
def test_filter_users_by_discipline(self):
    discipline = Discipline.objects.create(name='Pływanie')
    coach = Coach.objects.create(user=self.user)
    coach.disciplines.add(discipline)
    
    response = self.client.get(f'/api/users/?discipline={discipline.id}')
    assert response.status_code == 200
    assert self.user.id in [u['id'] for u in response.data['results']]
```

---

**Wersja:** 3.0 Production-Ready  
**Ostatnia aktualizacja:** Luty 2026  
**Django Version:** 5.x  
**Python:** 3.10+
