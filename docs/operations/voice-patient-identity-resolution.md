# Voice Patient Identity Resolution (Operations)

Status: **Implemented** — see the architecture doc for the current design and behavior.

The voice channel uses `resolve_patient_identity` and `confirm_patient_identity` Retell tools backed by `PatientIdentityResolutionService`. Patient lookup in the demo uses simplified identity matching based on name and date of birth, with email or phone used to disambiguate when needed. This is sufficient for the fictional scheduling workflow, but it is not production-grade healthcare identity verification.

## Related docs

- [Voice Patient Identity Resolution (architecture)](../architecture/voice-patient-identity-resolution.md)
- [Retell Master Prompt v2](retell-master-prompt-v2.md)
- [Retell Tool Descriptions](retell-tool-descriptions.md)
- [Configuration — Voice patient intake](../configuration.md#voice-patient-intake)
