from django.core.management.base import BaseCommand
from products.models import Product

SERVICES = [
    # WORKSPACE
    ("Workspace Monthly", 6000, "OTHER"),
    ("Workspace Daily", 500, "OTHER"),
    ("Workspace Hourly", 100, "OTHER"),

    # CYBER SERVICES
    ("Printing Coloured", 15, "PRINTING"),
    ("Printing Black", 10, "PRINTING"),
    ("Photocopy Coloured", 15, "PRINTING"),
    ("Photocopy Black", 10, "PRINTING"),
    ("ID Coloured", 30, "DOCUMENTS"),
    ("ID Black", 20, "DOCUMENTS"),
    ("Scanning", 20, "SCANNING"),
    ("Typesetting Without Printing", 40, "DOCUMENTS"),
    ("Lamination", 50, "PRINTING"),
    ("Binding 0-50 Pages", 50, "DOCUMENTS"),
    ("Binding 51-75 Pages", 75, "DOCUMENTS"),
    ("Binding 76-100 Pages", 100, "DOCUMENTS"),
    ("Binding 100-125 Pages", 125, "DOCUMENTS"),
    ("Passport Photos 8 pcs", 150, "DOCUMENTS"),
    ("Photo/Image A4 Glossy", 100, "PRINTING"),
    ("Matte Glossy Insurance Paper", 50, "PRINTING"),
    ("Glossy Sticker A4 Paper", 100, "PRINTING"),
    ("A4 Envelopes", 15, "DOCUMENTS"),
    ("A5 & B6 Envelopes", 10, "DOCUMENTS"),
    ("Browsing Per Minute", 1, "INTERNET"),
    ("CV Normal Format", 100, "DOCUMENTS"),
    ("CV Professional Format", 200, "DOCUMENTS"),
    ("Converting Docs to Word or Editing", 20, "DOCUMENTS"),

    # E-CITIZEN AND OTHER SERVICES
    ("Passport Application", 500, "GOVERNMENT"),
    ("E-Citizen Registration", 100, "GOVERNMENT"),
    ("DL Renewal", 150, "GOVERNMENT"),
    ("Good Conduct", 150, "GOVERNMENT"),
    ("Booking Inspection", 150, "GOVERNMENT"),
    ("E-Tims Invoice", 100, "GOVERNMENT"),
    ("Company Registration", 1000, "GOVERNMENT"),
    ("Business Name Registration", 500, "GOVERNMENT"),
    ("Birth/Marriage Certificates", 300, "GOVERNMENT"),
    ("File Returns Nil", 100, "GOVERNMENT"),
    ("File Returns with Employment Only", 200, "GOVERNMENT"),
    ("File Returns with Employment Only Plus", 300, "GOVERNMENT"),
    ("SHA Registration", 400, "GOVERNMENT"),
    ("Any Other Online Application", 100, "GOVERNMENT"),
    ("Vehicle Transfer", 300, "GOVERNMENT"),
    ("KRA PIN Creation", 500, "GOVERNMENT"),
    ("Email Creation", 200, "INTERNET"),
]

class Command(BaseCommand):
    help = "Seed Qubits Data Solutions services"

    def handle(self, *args, **options):
        created = 0
        updated = 0
        for name, price, category in SERVICES:
            obj, was_created = Product.objects.get_or_create(
                name=name,
                defaults={"price": price, "category": category, "stock": 9999, "is_service": True, "active": True},
            )
            if was_created:
                created += 1
            else:
                changed = False
                if obj.price != price:
                    obj.price = price
                    changed = True
                if obj.category != category:
                    obj.category = category
                    changed = True
                if not obj.is_service or not obj.active:
                    obj.is_service = True
                    obj.active = True
                    changed = True
                if changed:
                    obj.save()
                    updated += 1
        self.stdout.write(self.style.SUCCESS(f"Services seeded successfully. Created: {created}. Updated: {updated}. Total defined: {len(SERVICES)}."))
