from app.model_runtime import (
 validate_feature_payload
)

SCHEMA = {
 "type": "object",
 "required": [
 "hour",
 "temperature_c",
 "is_holiday"
 ],
 "additionalProperties": False,
 "properties": {
 "hour": {
 "type": "integer",
 "minimum": 0,
 "maximum": 23
 },
 "temperature_c": {
 "type": "number"
 },
 "is_holiday": {
 "type": "boolean"
 }
 }
}

def test_valid_input():
 payload = {
 "hour": 18,
 "temperature_c": 20.5,
 "is_holiday": False
 }
 assert validate_feature_payload(
 SCHEMA,
 payload
 ) == []

def test_missing_feature():
 payload = {
 "hour": 18,
 "temperature_c": 20.5
 }
 assert validate_feature_payload(
 SCHEMA,
 payload
 )

def test_invalid_hour():
 payload = {
 "hour": 30,
 "temperature_c": 20.5,
 "is_holiday": False
 }
 assert validate_feature_payload(
 SCHEMA,
 payload
 )

def test_wrong_boolean_type():
 payload = {
 "hour": 18,
 "temperature_c": 20.5,
 "is_holiday": "false"
 }
 assert validate_feature_payload(
 SCHEMA,
 payload
 )

def test_unexpected_feature():
 payload = {
 "hour": 18,
 "temperature_c": 20.5,
 "is_holiday": False,
 "unknown_feature": 123
 }
 assert validate_feature_payload(
 SCHEMA,
 payload
 )

def test_non_finite_number():
 payload = {
 "hour": 18,
 "temperature_c": float("nan"),
 "is_holiday": False
 }
 assert validate_feature_payload(
 SCHEMA,
 payload
 )
