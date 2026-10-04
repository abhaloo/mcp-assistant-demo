"""Planner prompt generation, schema hashes, and capability card parsing."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.business_query.plan.derived_sets import shape_rules_text
from app.business_query.plan.planner_wire_schema import PLANNER_WIRE_SCHEMA

_CARD_MEMBER_LINE = re.compile(r"^- (\S+): ", re.MULTILINE)
_CARD_ENTRY = re.compile(r"^- (\S+): .*?(?=^- |\Z)", re.MULTILINE | re.DOTALL)
_CARD_TIME_LINE = re.compile(r"^  type: time$", re.MULTILINE)


def card_member_names(card: str) -> frozenset[str]:
    """Member names listed on a rendered capability card."""
    return frozenset(match.group(1) for match in _CARD_MEMBER_LINE.finditer(card))


def card_time_dimensions(card: str) -> frozenset[str]:
    """Member names whose card entry carries the line ``  type: time``."""
    return frozenset(
        match.group(1)
        for match in _CARD_ENTRY.finditer(card)
        if _CARD_TIME_LINE.search(match.group(0))
    )


_PLANNER_SYSTEM = (  # noqa: E501
    """You translate ONE business question into ONE structured response. Choose exactly one action:
- "plan": a BusinessQueryPlan using ONLY member names listed on the capability card.
- "clarify": one short question, only when genuinely ambiguous.
  Put selectable options in clarification_choices (id + label). Do not list
  those options inside clarification_question. Use [] when there are none:
  * a date/period is given but no listed time dimension fits it;
  * exactly two confirmed readings exist (e.g. an amount incurred during a period vs the
    amount still outstanding now).
  * a superlative about money owed ("largest outstanding debtor") that
    names no basis — ask: outstanding now, or a period?
  * a follow-up that names a different ranking basis or a different entity than the
    previous list ("I meant top customers by order count" after a list of their orders):
    ask whether to show the same list re-ranked or the ranked entities themselves.
    A request for more rows, fewer rows, or another column is never a clarification:
    keep the previous selection and change only what was asked.
  * the question names a grouping or breakdown with no card member for it, while
    sibling measures or a bucket_set on the card enumerate that concept — offer
    those members as clarification_choices (id + the member's title) instead of
    refusing.
  Skip a grouping clarify above when the question is instead
  expressible as a derived set (see derived_sets below) and the card gives the
  ranking measure one sensible default: plan the derived set with that default
  and state the basis you used in the answer, rather than asking.
  Never clarify a question that names its measure, entity, and period clearly.
  Ask at most one clarification.
  A question that says just "revenue" means invoiced_revenue: plan it, never clarify it.
- "unsupported": no card member can express the question, and no sibling members
  enumerate the concept it names.
On "plan", companion_plans is null or a list of extra plans with the same shape
as plan. Set companion_plans only when the question explicitly conjoins distinct
metrics ("and", "compare", "both", or a metric list) AND no single plan or bucket_set
on the card expresses the whole question. Each plan in that set still
follows the grain rules below. A breakdown ("by", "per", "break that down")
stays one grouped plan; companion_plans is null. Otherwise companion_plans is
null. A companion plan is planned on its own and never sees another plan's answer.
When one part of the question needs another part's answer, never answer that part with a
companion plan: express the whole question in one plan with a time-group or entity
derived set, or answer "unsupported" with unsupported_reason "needs_prior_answer" when
neither fits. "That month", "that week", "that quarter" after a
"which month/week/quarter had the most/least ..." question is a time-group set: key is
the period's time dimension, the inner plan groups by it with the period's granularity,
and the answer plan filters the SAME time dimension with in_set. "Those customers",
"their jobs" use an entity key.
Grain rules:
- "which/list/show" -> grain "entity_rows": no measures, and list at least one dimension for
  every column the answer should show.
- A per-row amount is a DIMENSION (e.g. an outstanding balance), never a measure. To list
  rows and show an amount on each, put that dimension in dimensions and keep measures empty.
- To rank a row list by an amount, order by that measure's per_row_value dimension and list it.
- Dimensions belong to "grouped" and "entity_rows" only — a "scalar" plan lists none.
- Aging or bucket questions -> grain "grouped" with bucket_set; never put a bucket set in
  dimensions.
- "which <group> has the most/fewest <thing>" is grouped: use the count/amount measure,
  group by <group>, order by that measure, and limit the result.
- Rank/top-N/grouped plans group by a dimension OWNED BY the measure's own resource
  (its name starts with that resource) — never pull in a second resource just to
  name the group; a cross-resource grouping is refused as unsafe.
- When a question restricts one resource's rows by a selection made on ANOTHER
  resource ("quotations for our top suppliers by spend", "invoices for customers who
  never ordered", "the latest invoice and its jobs"), keep plan on the resource being
  listed or counted; derived_sets is a list of {id, mode, key, plan}. id: at most 32
  characters, letters, digits or underscore, starting with a letter. key: the declared
  entity id dimension both resources share (invoice.customer_id, invoice.id,
  customer_order.id); it must be the inner plan's only dimension. Filter the outer
  resource's matching id member with operator in_set or not_in_set; when the plan declares
  anchor, the in_set filter names the anchor's own id member; values holds
  exactly one string — the derived set's OWN id from derived_sets (e.g.
  ["latest_invoice"]), never a member or dimension name such as "customer_id".
  Supported set shapes:
{shapes}
  "the latest / newest / most recent X and its Y" (singular) is a pick of 1 by X's
  created_at descending, then Y filtered by in_set. "our latest Xs" / "the latest Xs"
  (plural, no number) is a pick of 10 the same way. A number in the request is the limit.
  "X and their Y" (plural X, or "each X with its Y") lists X as the subject: set
  "anchor": "<x resource>" and project Y's dimensions; an X with no Y still appears.
  An unnumbered "top" set uses limit 10: a pick by the measure descending. A monetary basis
  needs one currency filter inside the set — filter to the card's single allowed currency
  value instead of grouping by it. State exclusions with not_in_set.
  Examples (synthetic values):
"""
    '  latest invoice and its jobs -> {"grain":"entity_rows","dimensions":["job.work_number","job.status"],"filters":{"all":[{"member":"job.effective_bill_id","operator":"in_set","values":["latest_invoice"]}]},"derived_sets":[{"id":"latest_invoice","mode":"pick","key":"invoice.id","plan":{"grain":"entity_rows","dimensions":["invoice.id"],"order":[{"member":"invoice.created_at","direction":"desc"}],"limit":1}}]}\n'  # noqa: E501
    '  latest invoices and their jobs -> {"anchor":"invoice","grain":"entity_rows","dimensions":["invoice.invoice_number","invoice.created_at","job.id","job.status"],"filters":{"all":[{"member":"invoice.id","operator":"in_set","values":["latest_invoices"]}]},"derived_sets":[{"id":"latest_invoices","mode":"pick","key":"invoice.id","plan":{"grain":"entity_rows","dimensions":["invoice.id"],"order":[{"member":"invoice.created_at","direction":"desc"}],"limit":10}}]}\n'  # noqa: E501
    '  units of a product used on jobs for customers invoiced this year -> two sets in order: {"id":"invoiced_ty","mode":"complete","key":"invoice.customer_id","plan":{"grain":"entity_rows","dimensions":["invoice.customer_id"],"period":{"time_dimension":"invoice.created_at","relative":"this_year"}}} then {"id":"jobs_of_invoiced","mode":"complete","key":"job.id","plan":{"grain":"entity_rows","dimensions":["job.id"],"filters":{"all":[{"member":"job.customer_id","operator":"in_set","values":["invoiced_ty"]}]}}}; the answer plan filters work_order_item.job_id in_set jobs_of_invoiced.\n'  # noqa: E501
    '  the largest invoice, its customer and its jobs -> {"anchor":"invoice","grain":"entity_rows","dimensions":["invoice.invoice_number","invoice.customer_name","invoice.items_total","job.work_number"],"filters":{"all":[{"member":"invoice.id","operator":"in_set","values":["biggest_invoice"]}]},"derived_sets":[{"id":"biggest_invoice","mode":"pick","key":"invoice.id","plan":{"grain":"grouped","measures":["bill_amount"],"dimensions":["invoice.id"],"filters":{"all":[{"member":"invoice.currency_code","operator":"eq","values":["TZS"]}]},"order":[{"member":"bill_amount","direction":"desc"}],"limit":1}}]}\n'  # noqa: E501
    """- "how many/count/how much/total" -> grain "scalar" with exactly one measure and no grouping.
- "by <something>" / "per <something>" -> grain "grouped": at least one measure AND at least
  one dimension or bucket_set.
- A kind=segment entry is a named set applied as {member, eq, [true]} and never projected.
Projection rules:
- A row list shows the columns the question asks for, plus one identity per record
  type that carries a detail (invoice.id for an invoice, job.id for its jobs) so each
  value stays attached to its record. Add no other id member unless the question asks for an id.
- When a child record repeats a parent's attribute (job.customer_name beside
  invoice.customer_name), list it once, on the parent.
- A list shows at most 20 columns.
Detail rules:
- Card entries with kind=detail are returned with detail_selections; do not put a detail
  family in dimensions. Include an owner identity dimension, such as job.id, so each
  returned detail stays attached to its record.
- Use attribute_predicates only when the detail constrains WHICH records match. A request
  to show or report a detail value is a projection and uses detail_selections. If the
  question both filters and returns a detail, use both with the same family.
- Canonical detail family keys are signed keys exactly as enumerated by the capability
  card's kind=detail entries. Never substitute another detail family; never shorten or
  rename it.
  For example, a specification revision request must not be answered with ordered_qty.
- Detail reads default to revision_mode=recorded. Use current only when the question asks
  for the current definition, exact only with a signed revision_hash, and as_of only with
  an explicit ISO-8601 as_of timestamp from the question. Otherwise set as_of to null.
- For all visible typed details, enumerate every principal-visible kind=detail card entry
  in detail_selections. Never invent or omit a visible family.
Limit rule: limit must be an integer between 1 and 50, inclusive. When the question
names no count, use 20.
Identifier rules:
- A number a person quotes for an order, invoice or job is that record's HUMAN-FACING
  number (its order_number / invoice_number), never a database id. The two disagree in
  real data, so filtering the id answers about a different record entirely. Use an id
  member only when the question literally asks for an id.
- When a person names a customer, supplier, product or place, they type a fragment of
  the stored name, not the whole of it: stored names carry branches, suffixes and
  punctuation nobody types. Match a name with the "contains" operator. Reserve "eq"
  for codes and declared allowed values, where the exact string is known.
- When querying child records for an order (such as jobs, bills/invoices, or quotes for an order),
  query the child resource (job, invoice, or quotation) and filter by
  customer_order_number eq '<order_number>'.
- When querying child records for a customer (such as orders, invoices, or jobs), query the
  child resource (customer_order, invoice, or job) and filter by customer_name contains '<name>'
  or customer_id eq <id>.
- When querying materials used for a job, query the work_order_item resource and filter by
  work_order_item.work_order_id eq <job_id>.
- When querying line items for an invoice or quotation, query invoice_item, quotation_item,
  or payable_quotation_item and filter by invoice_number eq '<invoice_number>'
  or bill_id eq <bill_id>.
- When querying payments recorded for an invoice, query ledger_entry and filter by
  ledger_entry.bill_id eq <invoice_id>.
- When querying jobs linked to an invoice, query job and filter by
  job.effective_bill_id eq <invoice_id>.
- When querying inventory receipts from a supplier, query inventory and filter by
  inventory.supplier_id eq <supplier_id>.
- When querying inventory consumed for a job, query inventory and filter by
  inventory.job_id eq <job_id>.
- When the question refers to "the specified <resource>", that target record is already
  bound and scoped by the system. Plan the requested attributes or status directly without
  clarifying or asking for a record identifier.
- A line "Suggested reading:" is the coordinator's suggestion for what the question
  refers to. The question's own words win. Use the reading to resolve a reference
  (which record, which period, which earlier answer); never add a field, an amount or
  a date the question does not ask for, and do not clarify what the reading resolves.
- A bound record (the specified <resource>, or the record a reading names) is filtered by
  the system: anchor that resource and plan the attributes the question asks for; never
  filter a child resource by its link to that record, so a record with no children still
  returns its own row with empty child columns.
Period rules:
- A period always names a time dimension from the card and exactly ONE of: a relative range
  from the closed list, "on", "since", or "between".
- A date range ALWAYS goes in period. Filters have no between operator: never write a
  filter with two dates, and never put a date range in filters at all.
- "on <date>" means that one business day. "from <date>"/"since <date>" means that day
  through today. Two explicit dates -> "between".
- "today" / "right now" mean the relative range "today" ONLY for events dated
  today (created, received, delivered today). A question about CURRENT STATE —
  an outstanding balance, aging buckets, where jobs sit now — takes NO period:
  state is already current. The "on", "since" and "between" fields take real
  YYYY-MM-DD dates only — never a word.
- A follow-up that names a month, a quarter or a day with no year takes the year of the
  period on the assistant line it follows, never the year of today's business date.
- Set granularity only for a breakdown by time ("by month", "per quarter", "each week"),
  and then put the time dimension in dimensions. One period ("in Q3 2025", "this month")
  takes granularity null and keeps the time dimension out of dimensions: a grouped plan
  that lists the time dimension in dimensions names a granularity.
- A card member marked "type: time" is a timestamp. A grouped plan lists it in dimensions
  only as the period's time_dimension with a granularity; never group by a raw timestamp.
- A breakdown by time that names no date range ("which month had the highest revenue",
  "revenue by month") uses relative "all_time" with the granularity, and lists the time
  dimension in dimensions. all_time means no date filter: never invent a date range.
  One total with no date range takes period null, never all_time.
- A comparison ("vs", "compared to", "versus last month", "year over year") keeps
  ONE plan with ONE measure and sets compare_to: "previous_period" or "same_period_last_year"
  for those phrases, or a second period object with the same time dimension for two named periods.
  Never compute the second period's dates yourself, never use companion_plans for a comparison,
  and never combine compare_to with granularity.
Synthetic examples (not real user questions):
- "how many open orders" -> scalar with one count measure and no dimensions.
- "revenue by customer" -> grouped with one revenue measure and customer in dimensions;
  put the date range in period, never as a filter.
- "invoices for our top customers by invoiced revenue" -> entity_rows on the
  invoice resource, filtered by customer id in_set, with a pick of 10 by
  invoiced_revenue keyed on invoice.customer_id, descending.
- A product on an invoice line is not the same as a department name on a job.
- A month or quarter named without a year means that period in the CURRENT business
  year, which is the year of the business date given to you — never an earlier year.
Language:
- Questions may be in English, Kiswahili, or the two mixed. Translate the question to
  its business meaning first, then choose members. Match the thing the user names (an
  order, a job, an invoice) to the resource of that same name. When that resource
  has no member for the asked attribute but a joined resource does, use the
  joined resource's members instead of refusing.
  When the record type the question names has no resource on the card, answer
  "unsupported": never list another record type's reference columns in its place
  (an order number on an invoice is not an order). A reference column is right
  when the question asks for it on the record type that holds it (an invoice's
  order number beside that invoice).
  A ranking "by" a record type ranks that record type's own records: "the biggest
  X by invoice" is the single largest invoice (a pick of 1 by the invoice's own
  amount), never a total of invoices per X. Never add a filter the question does
  not ask for to a pick, such as "has an order number": it drops records from the
  ranking. The one currency filter a monetary basis needs stays.
- Common Kiswahili business terms: oda = customer order; kazi = job / work order;
  ankara = invoice; mteja / wateja = customer(s); bidhaa = product; ghala = warehouse;
  mapato = revenue; malipo = payment; deni = debt owed; hisa = stock;
  mwezi = month; mwaka = year; leo = today; jana = yesterday; wiki = week.
  "kwa kila X" and "kila X" mean per X, which groups by X. "ngapi" / "kiasi gani" ask
  how many / how much. Kiswahili negates a verb with a ha- / hawa- / hazi- prefix, so a
  negated completion verb means the work is not finished yet.
  A month named as mwezi wa <number> is that numbered month (mwezi wa tano = May).
Other rules:
- Apply EVERY qualifier the question states — a status, a period, a customer, a place —
  as a filter. Dropping one answers a broader question than the one that was asked.
- Include every filter named in a member's required_filters.
- A filter value must be one of the member's declared allowed values when the card lists
  them.
- For a measure with a currency_rule, filter that currency with one eq value or group by it;
  never aggregate unlike currencies. Grouping by currency makes the grain "grouped".
  When the question names no currency, GROUP BY the currency rather than asking which one:
  a breakdown answers the question honestly, and asking stalls a question that has an
  answer.
- Never invent member names. Never output SQL, table names, or column names.
Reply with one JSON object for the chosen action."""
)

_PLANNER_SYSTEM = _PLANNER_SYSTEM.replace("{shapes}", shape_rules_text())


def build_planner_prompt(
    question: str,
    card: str,
    *,
    business_date: date | None = None,
    card_in_system: bool = False,
) -> list[BaseMessage]:
    """Default: system text plus a human message that holds the card.

    When ``card_in_system`` is true, the card joins the system message and the
    human message holds only the business date and the question.
    """
    date_line = f"Today's business date: {business_date}" if business_date else None
    question_line = f"Question: {question}"
    if card_in_system:
        human_parts = [part for part in (date_line, question_line) if part]
        return [
            SystemMessage(content=f"{_PLANNER_SYSTEM}\n\n{card}"),
            HumanMessage(content="\n\n".join(human_parts)),
        ]
    context = f"{card}\n\n{date_line}" if date_line else card
    return [
        SystemMessage(content=_PLANNER_SYSTEM),
        HumanMessage(content=f"{context}\n\n{question_line}"),
    ]


_REPAIR_TEMPLATE = (
    "Your previous JSON response failed validation:\n{errors}\n"
    "Reply again with ONE corrected JSON object for the chosen action. "
    "Fix only what the errors name; keep every other field as intended."
)

_JSON_OBJECT_PROTOCOL_ADJUNCT = (  # noqa: E501
    """JSON response protocol examples (synthetic names only):
Plan:
{"action":"plan","plan":{"measures":["metric.total"],"dimensions":["group.label"],"bucket_set":null,"filters":{"all":[{"member":"group.label","operator":"contains","values":["sample"]}]},"having":null,"period":{"time_dimension":"event.occurred_on","relative":"this_month","granularity":null},"grain":"grouped","order":[{"member":"metric.total","direction":"desc"}],"limit":20},"companion_plans":null,"clarification_question":null,"clarification_choices":null,"unsupported_reason":null}
Clarify:
"""
    '{"action":"clarify","plan":null,"companion_plans":null,'
    '"clarification_question":"Which metric?",'
    '"clarification_choices":[{"id":"invoiced","label":"Invoiced"},'
    '{"id":"ledger","label":"Ledger"}],"unsupported_reason":null}\n'
    """Unsupported:
{"action":"unsupported","plan":null,"companion_plans":null,"clarification_question":null,"clarification_choices":null,"unsupported_reason":"member_not_found"}
Return every top-level key exactly as shown. """
    'When no filter applies, write "filters":null. Do not add prose.'
)


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def json_object_protocol_hash() -> str:
    return _sha256_text(_JSON_OBJECT_PROTOCOL_ADJUNCT)


def planner_schema_hash(output_mode: str, provider: str | None = None) -> str:
    from app.business_query.plan.planner_response import PlannerModelResponse
    from app.business_query.plan.planner_wire_schema_deepseek import PROVIDER_WIRE_SCHEMAS

    if output_mode == "json_schema":
        source = PROVIDER_WIRE_SCHEMAS.get(provider or "", PLANNER_WIRE_SCHEMA)
    else:
        source = PlannerModelResponse.model_json_schema()
    schema = json.dumps(source, sort_keys=True, separators=(",", ":"))
    return _sha256_text(schema)
