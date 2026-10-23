import json

from datetime import date, timedelta
from pathlib import Path


ROOT = Path.cwd()

PROJECT_CONTRACT = (
    ROOT / "config/project_contract.json"
)

OUTPUT = (
    ROOT / "config/feature_split_contract.json"
)


def get_first(mapping, *names):
    for name in names:
        value = mapping.get(name)

        if value is not None and value != "":
            return value

    return None


def next_day(value):
    return (
        date.fromisoformat(value)
        + timedelta(days=1)
    ).isoformat()


def previous_day(value):
    return (
        date.fromisoformat(value)
        - timedelta(days=1)
    ).isoformat()


def main():
    contract = json.loads(
        PROJECT_CONTRACT.read_text(
            encoding="utf-8"
        )
    )

    training = contract.get("training", {})

    dataset = contract["dataset"]

    if dataset["version"] != "dv001":
        raise ValueError(
            "Expected dataset version dv001"
        )

    start = training.get(
        "train_start",
        "2017-12-01"
    )

    end = training.get(
        "test_end",
        "2018-11-30"
    )

    explicit = {
        "train_end": get_first(
            training, "train_end"
        ),
        "val_start": get_first(
            training,
            "val_start",
            "validation_start"
        ),
        "val_end": get_first(
            training,
            "val_end",
            "validation_end"
        ),
        "test_start": get_first(
            training, "test_start"
        )
    }

    # Infer a directly adjacent boundary when
    # its counterpart is already defined.
    if explicit["train_end"] and not explicit["val_start"]:
        explicit["val_start"] = next_day(
            explicit["train_end"]
        )

    if explicit["val_start"] and not explicit["train_end"]:
        explicit["train_end"] = previous_day(
            explicit["val_start"]
        )

    if explicit["val_end"] and not explicit["test_start"]:
        explicit["test_start"] = next_day(
            explicit["val_end"]
        )

    if explicit["test_start"] and not explicit["val_end"]:
        explicit["val_end"] = previous_day(
            explicit["test_start"]
        )

    if all(explicit.values()):
        boundaries = {
            "train_start": start,
            "train_end": explicit["train_end"],
            "validation_start": explicit["val_start"],
            "validation_end": explicit["val_end"],
            "test_start": explicit["test_start"],
            "test_end": end
        }

        source = "existing-project-contract"

    elif any(explicit.values()):
        raise ValueError(
            "Partial split boundaries exist. "
            "Complete the split dates in "
            "config/project_contract.json."
        )

    else:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)

        total_days = (
            end_date - start_date
        ).days + 1

        train_ratio = float(
            training.get("train_ratio", 0.70)
        )

        validation_ratio = float(
            get_first(
                training,
                "val_ratio",
                "validation_ratio"
            ) or 0.15
        )

        test_ratio = float(
            training.get("test_ratio", 0.15)
        )

        if any(
            ratio <= 0
            for ratio in [
                train_ratio,
                validation_ratio,
                test_ratio
            ]
        ):
            raise ValueError(
                "Split ratios must be positive"
            )

        if abs(
            train_ratio + validation_ratio
            + test_ratio - 1.0
        ) > 1e-8:
            raise ValueError(
                "Split ratios must sum to 1.0"
            )

        train_days = int(total_days * train_ratio)
        val_days = int(total_days * validation_ratio)

        if min(
            train_days,
            val_days,
            total_days - train_days - val_days
        ) <= 0:
            raise ValueError(
                "Split period is too short"
            )

        train_end = (
            start_date
            + timedelta(days=train_days - 1)
        )

        validation_start = (
            train_end + timedelta(days=1)
        )

        validation_end = (
            validation_start
            + timedelta(days=val_days - 1)
        )

        test_start = (
            validation_end + timedelta(days=1)
        )

        boundaries = {
            "train_start": start,
            "train_end": train_end.isoformat(),
            "validation_start": (
                validation_start.isoformat()
            ),
            "validation_end": (
                validation_end.isoformat()
            ),
            "test_start": test_start.isoformat(),
            "test_end": end
        }

        source = "calendar-day-ratio-from-contract"

    parsed = {
        key: date.fromisoformat(value)
        for key, value in boundaries.items()
    }

    if not (
        parsed["train_start"]
        <= parsed["train_end"]
        < parsed["validation_start"]
        <= parsed["validation_end"]
        < parsed["test_start"]
        <= parsed["test_end"]
    ):
        raise ValueError(
            "Split date order is invalid"
        )

    if (
        parsed["train_end"] + timedelta(days=1)
        != parsed["validation_start"]
    ):
        raise ValueError(
            "Train-validation boundary has a gap"
        )

    if (
        parsed["validation_end"] + timedelta(days=1)
        != parsed["test_start"]
    ):
        raise ValueError(
            "Validation-test boundary has a gap"
        )

    if (
        parsed["train_start"].isoformat()
        != "2017-12-01"
        or parsed["test_end"].isoformat()
        != "2018-11-30"
    ):
        raise ValueError(
            "Split dates must cover the full "
            "dv001 historical dataset"
        )

    output = {
        "project": "bikepulse",
        "dataset_version": "dv001",
        "feature_version": "fv001",
        "strategy": "chronological-calendar-day",
        "boundary_source": source,
        "timezone": "Asia/Seoul",
        "timestamp_storage": "local-time-without-timezone",
        "boundaries": boundaries,
        "training_filter": {
            "is_functioning_day": True
        },
        "preprocessing_policy": (
            "Fit learned preprocessing only on Train"
        )
    }

    if OUTPUT.exists():
        existing = json.loads(
            OUTPUT.read_text(encoding="utf-8")
        )

        if existing != output:
            raise RuntimeError(
                "Existing split contract differs. "
                "Review before creating a new version."
            )

        print("SPLIT CONTRACT ALREADY EXISTS")

    else:
        OUTPUT.write_text(
            json.dumps(
                output,
                indent=2,
                ensure_ascii=False
            ) + "\n",
            encoding="utf-8"
        )

        print("SPLIT CONTRACT CREATED")

    print(
        json.dumps(
            boundaries,
            indent=2
        )
    )


if __name__ == "__main__":
    main()
