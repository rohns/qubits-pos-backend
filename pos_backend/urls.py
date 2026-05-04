from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse
from rest_framework_simplejwt.views import TokenRefreshView
from users.views import MyTokenObtainPairView, current_user

def home(request):
    return HttpResponse("Qubits Data Solutions POS Backend is running")

urlpatterns = [
    path("", home),
    path("admin/", admin.site.urls),
    path("api/auth/login/", MyTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/me/", current_user),
    path("api/products/", include("products.urls")),
    path("api/sales/", include("sales.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/expenses/", include("expenses.urls")),
    path("api/reports/", include("reports.urls")),
]

from django.http import HttpResponse

def home(request):
    return HttpResponse("Qubits Data Solutions POS Backend is running")

urlpatterns = [
    path("", home),
    path("admin/", admin.site.urls),
]