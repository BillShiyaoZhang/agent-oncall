from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OnCallEnvelope(_message.Message):
    __slots__ = ("version", "timestamp", "signature", "call_request", "call_response", "discovery_request", "discovery_response")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    CALL_REQUEST_FIELD_NUMBER: _ClassVar[int]
    CALL_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    DISCOVERY_REQUEST_FIELD_NUMBER: _ClassVar[int]
    DISCOVERY_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    version: str
    timestamp: int
    signature: str
    call_request: CallRequest
    call_response: CallResponse
    discovery_request: DiscoveryRequest
    discovery_response: DiscoveryResponse
    def __init__(self, version: _Optional[str] = ..., timestamp: _Optional[int] = ..., signature: _Optional[str] = ..., call_request: _Optional[_Union[CallRequest, _Mapping]] = ..., call_response: _Optional[_Union[CallResponse, _Mapping]] = ..., discovery_request: _Optional[_Union[DiscoveryRequest, _Mapping]] = ..., discovery_response: _Optional[_Union[DiscoveryResponse, _Mapping]] = ...) -> None: ...

class CallRequest(_message.Message):
    __slots__ = ("request_id", "caller_urn", "intent_name", "arguments_json", "token")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_URN_FIELD_NUMBER: _ClassVar[int]
    INTENT_NAME_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_JSON_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    caller_urn: str
    intent_name: str
    arguments_json: str
    token: CapabilityToken
    def __init__(self, request_id: _Optional[str] = ..., caller_urn: _Optional[str] = ..., intent_name: _Optional[str] = ..., arguments_json: _Optional[str] = ..., token: _Optional[_Union[CapabilityToken, _Mapping]] = ...) -> None: ...

class CallResponse(_message.Message):
    __slots__ = ("request_id", "success", "error_code", "error_message", "result_json")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    success: bool
    error_code: int
    error_message: str
    result_json: str
    def __init__(self, request_id: _Optional[str] = ..., success: bool = ..., error_code: _Optional[int] = ..., error_message: _Optional[str] = ..., result_json: _Optional[str] = ...) -> None: ...

class DiscoveryRequest(_message.Message):
    __slots__ = ("request_id", "query_urn", "category_filter")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    QUERY_URN_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FILTER_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    query_urn: str
    category_filter: str
    def __init__(self, request_id: _Optional[str] = ..., query_urn: _Optional[str] = ..., category_filter: _Optional[str] = ...) -> None: ...

class DiscoveryResponse(_message.Message):
    __slots__ = ("request_id", "intents")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INTENTS_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    intents: _containers.RepeatedCompositeFieldContainer[IntentMetadata]
    def __init__(self, request_id: _Optional[str] = ..., intents: _Optional[_Iterable[_Union[IntentMetadata, _Mapping]]] = ...) -> None: ...

class IntentMetadata(_message.Message):
    __slots__ = ("name", "description", "safe_description", "input_schema_json", "output_schema_json", "requires_hitl")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    SAFE_DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    INPUT_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    REQUIRES_HITL_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    safe_description: str
    input_schema_json: str
    output_schema_json: str
    requires_hitl: bool
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., safe_description: _Optional[str] = ..., input_schema_json: _Optional[str] = ..., output_schema_json: _Optional[str] = ..., requires_hitl: bool = ...) -> None: ...

class CapabilityToken(_message.Message):
    __slots__ = ("issuer_urn", "audience_urn", "expires_at", "constraints", "signature")
    ISSUER_URN_FIELD_NUMBER: _ClassVar[int]
    AUDIENCE_URN_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    CONSTRAINTS_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    issuer_urn: str
    audience_urn: str
    expires_at: int
    constraints: _containers.RepeatedCompositeFieldContainer[AllowedConstraint]
    signature: bytes
    def __init__(self, issuer_urn: _Optional[str] = ..., audience_urn: _Optional[str] = ..., expires_at: _Optional[int] = ..., constraints: _Optional[_Iterable[_Union[AllowedConstraint, _Mapping]]] = ..., signature: _Optional[bytes] = ...) -> None: ...

class AllowedConstraint(_message.Message):
    __slots__ = ("resource", "action", "filters")
    RESOURCE_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    resource: str
    action: str
    filters: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, resource: _Optional[str] = ..., action: _Optional[str] = ..., filters: _Optional[_Iterable[str]] = ...) -> None: ...
