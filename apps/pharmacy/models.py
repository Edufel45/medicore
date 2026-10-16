"""
MediCore — Pharmacy Models
============================================================
The drug catalogue, stock batches, and dispensing records.

Models:
  - Drug        : the master catalogue (name, price, reorder level)
  - StockBatch  : physical stock, tracked by expiry (FEFO)
  - Dispense    : one record per dispensed item

Design:
  - Drug is the *catalogue entry* — the name and the price.
  - StockBatch is *inventory* — actual physical units, per batch,
    with expiry dates. We use FEFO (First Expiry, First Out).
  - Dispense records which batch was used for which prescription.

Proposal reference:
  - §2.3 "drug stock management and automatic deduction on dispensing"
  - §2.3 "low-stock alerts and reorder management"
  - §2.3 "billing block where the system prevents dispensing of
    out-of-stock drugs that have been billed"
"""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


# ------------------------------------------------------------
# Drug
# ------------------------------------------------------------
class Drug(models.Model):
    """
    The master drug catalogue.

    Doctors type free text. The pharmacist maps it to one of these
    rows. The price lives here — the doctor never sees it.
    """

    name = models.CharField(
        max_length=120,
        unique=True,
        db_index=True,
        help_text="Canonical name, e.g. 'amoxicillin 500mg'.",
    )
    aliases = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated alternate names the doctor might type.",
    )
    price_ksh = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Retail price per unit, in Kenyan Shillings.",
    )
    unit = models.CharField(
        max_length=20,
        default="tablet",
        help_text="tablet, capsule, bottle, sachet, etc.",
    )

    # ---- Stock policy ----
    reorder_level = models.PositiveIntegerField(
        default=25,
        help_text="Low-stock alert when total stock <= this.",
    )
    is_controlled = models.BooleanField(
        default=False,
        help_text="Controlled substances need extra audit.",
    )

    # ---- Meta ----
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive drugs are hidden from the catalogue.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Drug"
        verbose_name_plural = "Drugs"

    def __str__(self):
        return f"{self.name} — Ksh {self.price_ksh}"

    # ---------------- Stock helpers ----------------
    @property
    def total_stock(self):
        """Total units across all non-expired batches."""
        today = timezone.localdate()
        agg = self.batches.filter(expiry_date__gte=today).aggregate(
            total=models.Sum("quantity_remaining")
        )
        return agg["total"] or 0

    @property
    def is_low_stock(self):
        return self.total_stock <= self.reorder_level

    @property
    def is_out_of_stock(self):
        return self.total_stock <= 0

    # ---------------- Alias matching ----------------
    def matches_typed_name(self, typed):
        """True if `typed` matches this drug's name or any alias."""
        if not typed:
            return False
        typed_lc = typed.strip().lower()
        if typed_lc == self.name.lower():
            return True
        if not self.aliases:
            return False
        for alias in [a.strip().lower() for a in self.aliases.split(",")]:
            if alias and alias == typed_lc:
                return True
        return False


# ------------------------------------------------------------
# StockBatch
# ------------------------------------------------------------
class StockBatch(models.Model):
    """
    A physical batch of stock. We track each batch separately so
    we can do FEFO (First Expiry, First Out) dispensing.

    When stock arrives, we create one StockBatch row.
    When we dispense, we decrement from the batch with the
    earliest expiry that still has quantity.
    """

    drug = models.ForeignKey(
        Drug,
        on_delete=models.PROTECT,
        related_name="batches",
    )
    batch_number = models.CharField(
        max_length=40,
        blank=True,
        help_text="Manufacturer batch/lot number (if known).",
    )
    quantity_received = models.PositiveIntegerField(
        help_text="Original quantity when batch was created.",
    )
    quantity_remaining = models.PositiveIntegerField(
        help_text="Units still in stock for this batch.",
    )
    expiry_date = models.DateField(
        db_index=True,
        help_text="FEFO target — earliest expiry is dispensed first.",
    )
    received_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batches_received",
    )

    class Meta:
        ordering = ["expiry_date"]
        verbose_name = "Stock batch"
        verbose_name_plural = "Stock batches"
        indexes = [
            models.Index(fields=["drug", "expiry_date"]),
        ]

    def __str__(self):
        return f"{self.drug.name} · batch {self.batch_number or '-'} · exp {self.expiry_date}"

    @property
    def is_expired(self):
        return self.expiry_date < timezone.localdate()

    @property
    def is_low(self):
        return 0 < self.quantity_remaining <= self.drug.reorder_level


# ------------------------------------------------------------
# Dispense
# ------------------------------------------------------------
class Dispense(models.Model):
    """
    Records a dispensed unit of a drug, tied to a specific batch
    and a specific prescription.

    Created by the pharmacist when they click "Dispense" in the
    pharmacy workbench.
    """

    prescription = models.ForeignKey(
        "clinical.Prescription",
        on_delete=models.PROTECT,
        related_name="dispense_records",
    )
    batch = models.ForeignKey(
        StockBatch,
        on_delete=models.PROTECT,
        related_name="dispense_records",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    dispensed_at = models.DateTimeField(auto_now_add=True)
    dispensed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dispenses_made",
    )

    class Meta:
        ordering = ["-dispensed_at"]
        verbose_name = "Dispense"
        verbose_name_plural = "Dispenses"

    def __str__(self):
        return f"{self.quantity} × {self.batch.drug.name} → {self.prescription.visit}"