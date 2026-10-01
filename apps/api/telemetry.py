"""OpenTelemetry setup (docs/spec.md section 10.6).

With no OTLP endpoint configured, the tracer is OpenTelemetry's built-in no-op,
so the rest of the code can create spans unconditionally.

Span attributes follow the OpenInference naming that Phoenix understands, which
is what makes a trace show up there as "retriever" and "LLM" steps with their
documents and prompts.
"""

import json

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configured = False


def init_tracing(otlp_endpoint: str, service_name: str = "campus-helpdesk-api") -> None:
    global _configured
    if _configured or not otlp_endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("campus-helpdesk")


def child_span(parent: trace.Span, name: str, kind: str) -> trace.Span:
    """Start a span under `parent` explicitly.

    The chat response is a generator that the server may resume on different
    threads, so "the current span" (which is tracked per thread) cannot be
    relied on. Passing the parent by hand avoids that.
    """
    span = get_tracer().start_span(name, context=trace.set_span_in_context(parent))
    span.set_attribute("openinference.span.kind", kind)
    return span


def trace_id_of(span: trace.Span) -> str | None:
    context = span.get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


def set_retrieved_documents(span: trace.Span, candidates: list) -> None:
    for index, candidate in enumerate(candidates):
        prefix = f"retrieval.documents.{index}.document"
        span.set_attribute(f"{prefix}.id", candidate.slug)
        span.set_attribute(f"{prefix}.content", candidate.text)
        span.set_attribute(f"{prefix}.score", float(candidate.score))


def set_llm_io(span: trace.Span, model: str, messages: list[dict], output: str) -> None:
    span.set_attribute("llm.model_name", model)
    span.set_attribute("input.value", json.dumps(messages, ensure_ascii=False))
    span.set_attribute("output.value", output)
