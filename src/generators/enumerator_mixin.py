from src.config import cfg
from src.modules.logging import log, log_error


class ErrorEnumerationMixin:
    """Mixin that adds error-enumeration capability to a program generator.

    Concrete generators should call ``_init_enumerator(options)`` during their
    ``__init__`` to opt into this capability.  Two integration patterns are
    supported:

    * **Iterator generators** (``APIDeclarationGenerator``, ``CFGGenerator``):
      call ``yield from self.enumerate_ill_typed_programs(program, program_id,
      api_namespace)`` inside their ``generate_ill_typed_programs`` method.

    * **Single-return generator** (``Generator``): the generator keeps a
      ``_pending_variants`` iterator and drains it one program at a time across
      successive ``generate()`` calls.  Parallel mode must not be combined with
      error enumeration (enforced at argument-parsing time).
    """

    def _init_enumerator(self, options: dict):
        """Initialise the error enumerator class from *options*.

        Sets ``self.ErrorEnumerator`` to the class resolved from
        ``options["error-enumerator"]``, or ``None`` if the key is absent /
        maps to an unknown name.  Also resets ``error_injected`` and the
        pending-variants state used by the single-return pattern.
        """
        from src.enumerators import get_error_enumerator
        self.ErrorEnumerator = get_error_enumerator(
            options.get("error-enumerator"))
        self.error_injected = None
        self._pending_variants = None
        self._skeleton_program_id = None
        self.error_enum_logger = None
        self.enumerator_options = {}

    def enumerate_ill_typed_programs(self, program, program_id,
                                     api_namespace=None):
        """Yield every ill-typed variant that can be derived from *program*.

        This is the single authoritative implementation of the enumeration
        loop.  Iterator generators use it via ``yield from``; the
        ``Generator`` wraps it with ``next()`` calls to serve one program per
        ``generate()`` invocation.

        Parameters
        ----------
        program:
            The well-typed skeleton to enumerate from.
        program_id:
            Identifier used only for log messages.
        api_namespace:
            Optional namespace string logged alongside each variant (used by
            API-driven generators).
        """
        error_enum = self.ErrorEnumerator(
            program, self, self.bt_factory, options=self.enumerator_options)
        flag = False
        enum_logger = self.error_enum_logger or self.logger
        try:
            cfg.substitute_wildcards = False
            for j, p in enumerate(error_enum.enumerate_programs()):
                if p is not None:
                    flag = True
                    self.error_injected = error_enum.error_explanation
                    log(enum_logger,
                        f"Enumerating error program {j + 1}"
                        f" for skeleton {program_id}\n")
                    if api_namespace is not None:
                        log(enum_logger, f"API namespace: {api_namespace}")
                    log(enum_logger, self.error_injected)
                    yield p
            metadata = error_enum.metadata
            log(enum_logger,
                f"Metadata for skeleton {program_id}:"
                f" locations: {metadata['locations']}"
                f" examined locations: {metadata['examined']}")
            if not flag:
                log(enum_logger,
                    f"No error added to skeleton {program_id}")
            cfg.substitute_wildcards = True
        except Exception as exc:
            raise exc
            log_error(enum_logger, exc)
            cfg.substitute_wildcards = True
