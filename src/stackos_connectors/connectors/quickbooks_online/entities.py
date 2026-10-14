"""Closed accounting read descriptors; caller text never selects SQL or a path."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

type ReadKind = Literal["transaction", "reference", "singleton"]


@dataclass(frozen=True)
class Entity:
    name: str
    kind: ReadKind
    query_name: str
    response_keys: tuple[str, ...]
    include_inactive: bool = False
    strings: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    booleans: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()


def _transaction(
    name: str,
    *,
    references: tuple[str, ...] = (),
    numbers: tuple[str, ...] = ("TotalAmt",),
    strings: tuple[str, ...] = (),
    booleans: tuple[str, ...] = (),
    dates: tuple[str, ...] = (),
    query_name: str | None = None,
    response_keys: tuple[str, ...] | None = None,
) -> Entity:
    return Entity(
        name,
        "transaction",
        query_name or name,
        response_keys or (name,),
        strings=("DocNumber", *strings),
        numbers=(*numbers, "ExchangeRate"),
        booleans=booleans,
        references=(*references, "CurrencyRef"),
        dates=dates,
    )


def _reference(
    name: str,
    *,
    strings: tuple[str, ...],
    numbers: tuple[str, ...] = (),
    references: tuple[str, ...] = (),
    include_inactive: bool = True,
) -> Entity:
    return Entity(
        name,
        "reference",
        name,
        (name,),
        include_inactive=include_inactive,
        strings=strings,
        numbers=numbers,
        references=references,
        booleans=("Active",) if include_inactive else (),
    )


_ENTITIES = (
    # Preserve the original invoice validation contract; other native fields stay raw.
    Entity(
        "Invoice",
        "transaction",
        "Invoice",
        ("Invoice",),
        numbers=("TotalAmt", "Balance"),
        references=("CustomerRef", "CurrencyRef"),
        dates=("DueDate",),
    ),
    _transaction("Payment", references=("CustomerRef",), strings=("PaymentType",)),
    _transaction(
        "SalesReceipt",
        references=("CustomerRef", "DepositToAccountRef"),
        numbers=("TotalAmt", "Balance"),
    ),
    _transaction(
        "RefundReceipt",
        references=("CustomerRef", "DepositToAccountRef"),
        numbers=("TotalAmt", "Balance"),
    ),
    _transaction(
        "CreditMemo",
        references=("CustomerRef", "DepositToAccountRef"),
        numbers=("TotalAmt", "Balance"),
    ),
    _transaction(
        "Bill",
        references=("VendorRef", "APAccountRef"),
        numbers=("TotalAmt", "Balance"),
        dates=("DueDate",),
    ),
    _transaction("BillPayment", references=("VendorRef",), strings=("PayType",)),
    _transaction(
        "Purchase",
        references=("AccountRef", "EntityRef"),
        strings=("PaymentType",),
        booleans=("Credit",),
    ),
    _transaction("VendorCredit", references=("VendorRef", "APAccountRef")),
    _transaction("Deposit", references=("DepositToAccountRef",)),
    _transaction("Transfer", references=("FromAccountRef", "ToAccountRef"), numbers=("Amount",)),
    _transaction("JournalEntry"),
    _transaction("Estimate", references=("CustomerRef",)),
    _transaction("PurchaseOrder", references=("VendorRef",)),
    _transaction(
        "CreditCardPayment",
        references=("BankAccountRef", "CCAccountRef"),
        numbers=("Amount",),
        query_name="creditcardpayment",
        response_keys=("CreditCardPayment", "CreditCardPaymentTxn"),
    ),
    _transaction("InventoryAdjustment"),
    _reference(
        "Account",
        strings=("Name", "FullyQualifiedName", "AccountType", "AccountSubType", "Classification"),
        numbers=("CurrentBalance",),
        references=("ParentRef", "CurrencyRef"),
    ),
    _reference(
        "Customer", strings=("DisplayName", "CompanyName"), references=("ParentRef", "CurrencyRef")
    ),
    _reference("Vendor", strings=("DisplayName", "CompanyName"), references=("CurrencyRef",)),
    _reference("Employee", strings=("DisplayName", "CompanyName")),
    _reference(
        "Item",
        strings=("Name", "FullyQualifiedName", "Type"),
        numbers=("UnitPrice", "PurchaseCost"),
        references=("ParentRef", "IncomeAccountRef", "ExpenseAccountRef", "AssetAccountRef"),
    ),
    _reference("Class", strings=("Name", "FullyQualifiedName"), references=("ParentRef",)),
    _reference("Department", strings=("Name", "FullyQualifiedName"), references=("ParentRef",)),
    _reference("PaymentMethod", strings=("Name",)),
    _reference(
        "Term",
        strings=("Name",),
        numbers=(
            "DueDays",
            "DiscountPercent",
            "DiscountDays",
            "DayOfMonthDue",
            "DueNextMonthDays",
            "DiscountDayOfMonth",
        ),
    ),
    # These tax entities reject Active query predicates; retain supplied field validation.
    Entity(
        "TaxCode", "reference", "TaxCode", ("TaxCode",), strings=("Name",), booleans=("Active",)
    ),
    Entity(
        "TaxRate",
        "reference",
        "TaxRate",
        ("TaxRate",),
        strings=("Name",),
        numbers=("RateValue",),
        booleans=("Active",),
    ),
    _reference("TaxAgency", strings=("DisplayName",), include_inactive=False),
    Entity(
        "CompanyInfo",
        "singleton",
        "",
        ("CompanyInfo",),
        strings=("CompanyName", "LegalName", "Country", "FiscalYearStartMonth"),
    ),
    Entity("Preferences", "singleton", "", ("Preferences",)),
)
ENTITIES = MappingProxyType({entity.name: entity for entity in _ENTITIES})
