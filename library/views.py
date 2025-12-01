
from datetime import timedelta
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from .models import Author, Book, Member, Loan
from .serializers import AuthorSerializer, BookSerializer, MemberSerializer, LoanSerializer, ExtendDueDate
from rest_framework.decorators import action
from django.utils import timezone
from django.db.models import Count, Q
from .tasks import send_loan_notification


class CustomPagination(PageNumberPagination):
    page_size = 'page_size'
    page_size_query_param = 1000
class AuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

class BookViewSet(viewsets.ModelViewSet):
    queryset = Book.objects.all().select_related('author')
    serializer_class = BookSerializer
    pagination_class = CustomPagination

    @action(detail=True, methods=['post'])
    def loan(self, request, pk=None):
        book = self.get_object()
        if book.available_copies < 1:
            return Response({'error': 'No available copies.'}, status=status.HTTP_400_BAD_REQUEST)
        member_id = request.data.get('member_id')
        try:
            member = Member.objects.get(id=member_id)
        except Member.DoesNotExist:
            return Response({'error': 'Member does not exist.'}, status=status.HTTP_400_BAD_REQUEST)
        loan = Loan.objects.create(book=book, member=member)
        book.available_copies -= 1
        book.save()
        send_loan_notification.delay(loan.id)
        return Response({'status': 'Book loaned successfully.'}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def return_book(self, request, pk=None):
        book = self.get_object()
        member_id = request.data.get('member_id')
        try:
            loan = Loan.objects.get(book=book, member__id=member_id, is_returned=False)
        except Loan.DoesNotExist:
            return Response({'error': 'Active loan does not exist.'}, status=status.HTTP_400_BAD_REQUEST)
        loan.is_returned = True
        loan.return_date = timezone.now().date()
        loan.save()
        book.available_copies += 1
        book.save()
        return Response({'status': 'Book returned successfully.'}, status=status.HTTP_200_OK)

class MemberViewSet(viewsets.ModelViewSet):
    queryset = Member.objects.all().select_related('user')
    serializer_class = MemberSerializer

    @action(detail=False, methods=['get'], url_path='top-active')
    def top_five_members(self, request):
        members = Member.objects.all().annotate(
            'active_loan', Count('loans', filter=Q(loans__is_returned=False))
        ).order_by('-active_loan').select_related('user')[:5]
        response = list()
        for member in members:
            response.append(
                {
                    'id': member.id,
                    'email': member.user.email,
                    'username': member.user.username,
                    'active_loan': member.active_loan
                }
            )
        return Response(response, status=status.HTTP_200_OK)

class LoanViewSet(viewsets.ModelViewSet):
    queryset = Loan.objects.all()
    serializer_class = LoanSerializer

    @action(detail=True, methods=['post'])
    def extend_due_date(self, request, pk=None):
        loan = self.get_object()
        if loan.due_date < timezone.now().date():
            return Response({'error': 'Due Date already been missed'}, status=status.HTTP_400_BAD_REQUEST)
        serialized = ExtendDueDate(data=request.data)
        serialized.is_valid(raise_exception=True)
        loan.due_date = loan.due_date + timedelta(days=serialized.validated_data['additional_days'])
        loan.save()
        return Response(LoanSerializer(loan).data, status=status.HTTP_200_OK)
