from django.core.management.base import BaseCommand, CommandError

from api.model_runtime import VllmRuntimeError, warmup_vllm_models


class Command(BaseCommand):
    help = "Start managed vLLM containers one at a time, wait until ready, then sleep them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            action="append",
            dest="models",
            help="Warm only this model. Repeat the option to warm multiple models.",
        )
        parser.add_argument(
            "--no-sleep",
            action="store_true",
            help="Leave the warmed model awake instead of sleeping it after readiness.",
        )

    def handle(self, *args, **options):
        models = options.get("models")
        sleep_after = not options["no_sleep"]

        try:
            warmed_models = warmup_vllm_models(models=models, sleep_after=sleep_after)
        except VllmRuntimeError as exc:
            raise CommandError(str(exc)) from exc

        if not warmed_models:
            self.stdout.write(self.style.WARNING("No managed vLLM models are configured."))
            return

        suffix = "and slept" if sleep_after else "and left awake"
        self.stdout.write(
            self.style.SUCCESS(
                f"Warmed {len(warmed_models)} vLLM model(s) {suffix}: "
                + ", ".join(warmed_models)
            )
        )
