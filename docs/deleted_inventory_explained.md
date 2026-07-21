# Deleted Inventory: Purpose and Replacement

Scope: cleanup commits after checkpoint `d2e2d890` through `28aac68d3ead7c105fc2012c61df34fe620f0767`. Source: July 20 reviewed audit, resolution ledger, current Git history. �Supposed purpose� is inferred from old names, contracts, tests, and call evidence.

## List 1 � Deleted files and renamed-away paths

1. **`contracts/app_content.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for app content.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

2. **`contracts/app_logging.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for app logging.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

3. **`contracts/artifact_cleanup.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for artifact cleanup.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

4. **`contracts/candidate_profile.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for candidate profile.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

5. **`contracts/candidate_title.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for candidate title.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

6. **`contracts/config_adapters.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for config adapters.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

7. **`contracts/dashboard_today.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for dashboard today.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

8. **`contracts/director_email_draft.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for director email draft.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

9. **`contracts/director_referral_service.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for director referral service.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

10. **`contracts/docx_compat.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for docx compat.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

11. **`contracts/integration_export.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for integration export.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

12. **`contracts/interview_app___init__.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app  init.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

13. **`contracts/interview_app_audio_devices.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app audio devices.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

14. **`contracts/interview_app_audio_runtime.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app audio runtime.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

15. **`contracts/interview_app_dashboard_controller.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app dashboard controller.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

16. **`contracts/interview_app_finalize_context.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app finalize context.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

17. **`contracts/interview_app_finalize_gateways.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app finalize gateways.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

18. **`contracts/interview_app_finalize_pipeline.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app finalize pipeline.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

19. **`contracts/interview_app_flow_controller.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app flow controller.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

20. **`contracts/interview_app_history_actions.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app history actions.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

21. **`contracts/interview_app_history_controller.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app history controller.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

22. **`contracts/interview_app_session_context.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app session context.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

23. **`contracts/interview_app_session_manager.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app session manager.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

24. **`contracts/interview_app_state.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app state.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

25. **`contracts/interview_app_transcript_processor.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app transcript processor.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

26. **`contracts/interview_app_transcript_writer.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app transcript writer.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

27. **`contracts/interview_app_transcription_executor.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app transcription executor.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

28. **`contracts/interview_app_transcription_queue.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app transcription queue.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

29. **`contracts/interview_app_types.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app types.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

30. **`contracts/interview_app_whisper_runtime_policy.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview app whisper runtime policy.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

31. **`contracts/interview_session_store.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview session store.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

32. **`contracts/interview_state.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for interview state.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

33. **`contracts/keyboard_telemetry.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for keyboard telemetry.
   - Replacement/status: Removed with retired module/product; system and architecture contracts now point only to supported code.

34. **`contracts/offer_letter.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for offer letter.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

35. **`contracts/onboarding_action_sections.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding action sections.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

36. **`contracts/onboarding_dashboard_actions.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding dashboard actions.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

37. **`contracts/onboarding_launch.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding launch.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

38. **`contracts/onboarding_models.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding models.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

39. **`contracts/onboarding_notifier.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding notifier.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

40. **`contracts/onboarding_reminder_health.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding reminder health.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

41. **`contracts/onboarding_reminder_runner.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding reminder runner.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

42. **`contracts/onboarding_scheduler.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding scheduler.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

43. **`contracts/onboarding_scheduler_dialog.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding scheduler dialog.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

44. **`contracts/onboarding_scheduler_status.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding scheduler status.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

45. **`contracts/onboarding_send_guardrails.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding send guardrails.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

46. **`contracts/onboarding_storage.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding storage.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

47. **`contracts/onboarding_store_v2.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding store v2.
   - Replacement/status: Replaced by renamed/migrated file `contracts/onboarding_store.contract.yaml`.

48. **`contracts/onboarding_task_filters.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding task filters.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

49. **`contracts/onboarding_template_reference.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding template reference.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

50. **`contracts/onboarding_ui_helpers.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for onboarding ui helpers.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `onboarding_operations`.

51. **`contracts/path_validation.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for path validation.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

52. **`contracts/referral_packet.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for referral packet.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

53. **`contracts/reporting.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for reporting.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

54. **`contracts/storage_utils.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for storage utils.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

55. **`contracts/template_placeholders.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for template placeholders.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

56. **`contracts/trait_definition_loader.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for trait definition loader.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

57. **`contracts/trait_scoring_adapter.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for trait scoring adapter.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

58. **`contracts/trait_signal_schema.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for trait signal schema.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

59. **`contracts/trait_signal_state.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for trait signal state.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

60. **`contracts/transcript_accumulator.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for transcript accumulator.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

61. **`contracts/transcription_diagnostics.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for transcription diagnostics.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `interview_runtime`.

62. **`contracts/ui_mode_switch.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for ui mode switch.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `PySide-only setup and launch flow`.

63. **`contracts/ux_metrics.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for ux metrics.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `platform_services`.

64. **`contracts/web_app.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for web app.
   - Replacement/status: Removed with retired module/product; system and architecture contracts now point only to supported code.

65. **`contracts/web_app_backend.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for web app backend.
   - Replacement/status: Removed with old module; canonical behavior is contracted under `scoring_reporting`.

66. **`contracts/web_scored_question.contract.yaml`**
   - Supposed purpose: Machine-readable interface contract for web scored question.
   - Replacement/status: Removed with retired module/product; system and architecture contracts now point only to supported code.

67. **`src/app_content.py`**
   - Supposed purpose: Compatibility wrapper exposing app content APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

68. **`src/app_logging.py`**
   - Supposed purpose: Compatibility wrapper exposing app logging APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

69. **`src/artifact_cleanup.py`**
   - Supposed purpose: Compatibility wrapper exposing artifact cleanup APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

70. **`src/candidate_profile.py`**
   - Supposed purpose: Compatibility wrapper exposing candidate profile APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

71. **`src/candidate_title.py`**
   - Supposed purpose: Compatibility wrapper exposing candidate title APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

72. **`src/config_adapters.py`**
   - Supposed purpose: Compatibility wrapper exposing config adapters APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

73. **`src/dashboard_today.py`**
   - Supposed purpose: Compatibility wrapper exposing dashboard today APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

74. **`src/director_email_draft.py`**
   - Supposed purpose: Compatibility wrapper exposing director email draft APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

75. **`src/director_referral_service.py`**
   - Supposed purpose: Compatibility wrapper exposing director referral service APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

76. **`src/docx_compat.py`**
   - Supposed purpose: Compatibility wrapper exposing docx compat APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

77. **`src/integration_export.py`**
   - Supposed purpose: Compatibility wrapper exposing integration export APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

78. **`src/interview_app/__init__.py`**
   - Supposed purpose: Package marker and import surface for interview app.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

79. **`src/interview_app/audio_devices.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.audio devices APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

80. **`src/interview_app/audio_runtime.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.audio runtime APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

81. **`src/interview_app/dashboard_controller.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.dashboard controller APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

82. **`src/interview_app/finalize_context.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.finalize context APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

83. **`src/interview_app/finalize_gateways.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.finalize gateways APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

84. **`src/interview_app/finalize_pipeline.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.finalize pipeline APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

85. **`src/interview_app/flow_controller.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.flow controller APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

86. **`src/interview_app/history_actions.py`**
   - Supposed purpose: Implemented interview app.history actions behavior or compatibility API.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

87. **`src/interview_app/history_controller.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.history controller APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

88. **`src/interview_app/session_context.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.session context APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

89. **`src/interview_app/session_manager.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.session manager APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

90. **`src/interview_app/state.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.state APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

91. **`src/interview_app/transcript_processor.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.transcript processor APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

92. **`src/interview_app/transcript_writer.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.transcript writer APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

93. **`src/interview_app/transcription_executor.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.transcription executor APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

94. **`src/interview_app/transcription_queue.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.transcription queue APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

95. **`src/interview_app/types.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.types APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

96. **`src/interview_app/whisper_runtime_policy.py`**
   - Supposed purpose: Compatibility wrapper exposing interview app.whisper runtime policy APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

97. **`src/interview_session_store.py`**
   - Supposed purpose: Compatibility wrapper exposing interview session store APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

98. **`src/interview_state.py`**
   - Supposed purpose: Compatibility wrapper exposing interview state APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

99. **`src/keyboard_telemetry.py`**
   - Supposed purpose: Collected keyboard activity telemetry for interview UI diagnostics.
   - Replacement/status: No replacement. Feature had zero runtime, dynamic, launcher, contract-lock, or test-required callers.

100. **`src/offer_letter.py`**
   - Supposed purpose: Compatibility wrapper exposing offer letter APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

101. **`src/onboarding_action_sections.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding action sections APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

102. **`src/onboarding_dashboard_actions.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding dashboard actions APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

103. **`src/onboarding_launch.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding launch APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

104. **`src/onboarding_models.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding models APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

105. **`src/onboarding_notifier.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding notifier APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

106. **`src/onboarding_reminder_health.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding reminder health APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

107. **`src/onboarding_reminder_runner.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding reminder runner APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

108. **`src/onboarding_scheduler.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding scheduler APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

109. **`src/onboarding_scheduler_dialog.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding scheduler dialog APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

110. **`src/onboarding_scheduler_status.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding scheduler status APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

111. **`src/onboarding_send_guardrails.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding send guardrails APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

112. **`src/onboarding_storage.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding storage APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

113. **`src/onboarding_task_filters.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding task filters APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

114. **`src/onboarding_template_reference.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding template reference APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

115. **`src/onboarding_ui_helpers.py`**
   - Supposed purpose: Compatibility wrapper exposing onboarding ui helpers APIs from canonical implementation.
   - Replacement/status: Already replaced by `onboarding_operations`; old module only duplicated or forwarded behavior.

116. **`src/path_validation.py`**
   - Supposed purpose: Compatibility wrapper exposing path validation APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

117. **`src/referral_packet.py`**
   - Supposed purpose: Compatibility wrapper exposing referral packet APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

118. **`src/reporting.py`**
   - Supposed purpose: Compatibility wrapper exposing reporting APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

119. **`src/storage_utils.py`**
   - Supposed purpose: Compatibility wrapper exposing storage utils APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

120. **`src/template_placeholders.py`**
   - Supposed purpose: Compatibility wrapper exposing template placeholders APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

121. **`src/trait_definition_loader.py`**
   - Supposed purpose: Compatibility wrapper exposing trait definition loader APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

122. **`src/trait_scoring_adapter.py`**
   - Supposed purpose: Compatibility wrapper exposing trait scoring adapter APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

123. **`src/trait_signal_schema.py`**
   - Supposed purpose: Compatibility wrapper exposing trait signal schema APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

124. **`src/trait_signal_state.py`**
   - Supposed purpose: Compatibility wrapper exposing trait signal state APIs from canonical implementation.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

125. **`src/transcript_accumulator.py`**
   - Supposed purpose: Compatibility wrapper exposing transcript accumulator APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

126. **`src/transcription_diagnostics.py`**
   - Supposed purpose: Compatibility wrapper exposing transcription diagnostics APIs from canonical implementation.
   - Replacement/status: Already replaced by `interview_runtime`; old module only duplicated or forwarded behavior.

127. **`src/ui_mode_switch.py`**
   - Supposed purpose: Selected between legacy UI modes during setup or launch.
   - Replacement/status: Already replaced by `PySide-only setup and launch flow`; old module only duplicated or forwarded behavior.

128. **`src/ux_metrics.py`**
   - Supposed purpose: Compatibility wrapper exposing ux metrics APIs from canonical implementation.
   - Replacement/status: Already replaced by `platform_services`; old module only duplicated or forwarded behavior.

129. **`src/web_app_backend.py`**
   - Supposed purpose: Served backend APIs for unsupported browser interview prototype.
   - Replacement/status: Already replaced by `scoring_reporting`; old module only duplicated or forwarded behavior.

130. **`tests/test_app_content.py`**
   - Supposed purpose: Test coverage for app content.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_content.py`.

131. **`tests/test_app_logging.py`**
   - Supposed purpose: Test coverage for app logging.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_logging.py`.

132. **`tests/test_app_logging_crash_report.py`**
   - Supposed purpose: Test coverage for app logging crash report.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_crash_report.py`.

133. **`tests/test_artifact_cleanup.py`**
   - Supposed purpose: Test coverage for artifact cleanup.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_artifact_cleanup.py`.

134. **`tests/test_candidate_profile.py`**
   - Supposed purpose: Test coverage for candidate profile.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_candidate_profile.py`.

135. **`tests/test_candidate_title.py`**
   - Supposed purpose: Test coverage for candidate title.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_candidate_title.py`.

136. **`tests/test_config_adapters.py`**
   - Supposed purpose: Test coverage for config adapters.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_config_adapters.py`.

137. **`tests/test_dashboard_today.py`**
   - Supposed purpose: Test coverage for dashboard today.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_dashboard_today.py`.

138. **`tests/test_director_email_draft.py`**
   - Supposed purpose: Test coverage for director email draft.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_director_email_draft.py`.

139. **`tests/test_director_referral_service.py`**
   - Supposed purpose: Test coverage for director referral service.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_director_referral_service.py`.

140. **`tests/test_integration_export.py`**
   - Supposed purpose: Test coverage for integration export.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_integration_export.py`.

141. **`tests/test_interview_app_audio_devices.py`**
   - Supposed purpose: Test coverage for interview app audio devices.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_audio_devices.py`.

142. **`tests/test_interview_app_audio_runtime.py`**
   - Supposed purpose: Test coverage for interview app audio runtime.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_audio_runtime.py`.

143. **`tests/test_interview_app_contract_interfaces.py`**
   - Supposed purpose: Test coverage for interview app contract interfaces.
   - Replacement/status: Removed with obsolete interface/product. Unique behavior coverage was migrated to canonical-owner tests when behavior remained.

144. **`tests/test_interview_app_controllers.py`**
   - Supposed purpose: Test coverage for interview app controllers.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_controllers.py`.

145. **`tests/test_interview_app_session_context.py`**
   - Supposed purpose: Test coverage for interview app session context.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_session_context.py`.

146. **`tests/test_interview_app_session_manager.py`**
   - Supposed purpose: Test coverage for interview app session manager.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_session_manager.py`.

147. **`tests/test_interview_app_transcript_processor.py`**
   - Supposed purpose: Test coverage for interview app transcript processor.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_transcript_processor.py`.

148. **`tests/test_interview_session_store.py`**
   - Supposed purpose: Test coverage for interview session store.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_session_store.py`.

149. **`tests/test_offer_letter.py`**
   - Supposed purpose: Test coverage for offer letter.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_offer_letter.py`.

150. **`tests/test_onboarding_action_sections.py`**
   - Supposed purpose: Test coverage for onboarding action sections.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_action_sections.py`.

151. **`tests/test_onboarding_dashboard_actions.py`**
   - Supposed purpose: Test coverage for onboarding dashboard actions.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_dashboard_actions.py`.

152. **`tests/test_onboarding_launch.py`**
   - Supposed purpose: Test coverage for onboarding launch.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_launch.py`.

153. **`tests/test_onboarding_models.py`**
   - Supposed purpose: Test coverage for onboarding models.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_models.py`.

154. **`tests/test_onboarding_notifier.py`**
   - Supposed purpose: Test coverage for onboarding notifier.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_notifier.py`.

155. **`tests/test_onboarding_reminder_health.py`**
   - Supposed purpose: Test coverage for onboarding reminder health.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_reminder_health.py`.

156. **`tests/test_onboarding_reminder_runner.py`**
   - Supposed purpose: Test coverage for onboarding reminder runner.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_reminder_runner.py`.

157. **`tests/test_onboarding_scheduler.py`**
   - Supposed purpose: Test coverage for onboarding scheduler.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_scheduler.py`.

158. **`tests/test_onboarding_scheduler_dialog.py`**
   - Supposed purpose: Test coverage for onboarding scheduler dialog.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_scheduler_dialog.py`.

159. **`tests/test_onboarding_scheduler_status.py`**
   - Supposed purpose: Test coverage for onboarding scheduler status.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_scheduler_status.py`.

160. **`tests/test_onboarding_send_guardrails.py`**
   - Supposed purpose: Test coverage for onboarding send guardrails.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_send_guardrails.py`.

161. **`tests/test_onboarding_ui_helpers.py`**
   - Supposed purpose: Test coverage for onboarding ui helpers.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_onboarding_operations_ui_helpers.py`.

162. **`tests/test_path_validation.py`**
   - Supposed purpose: Test coverage for path validation.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_path_validation.py`.

163. **`tests/test_referral_packet.py`**
   - Supposed purpose: Test coverage for referral packet.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_referral_packet.py`.

164. **`tests/test_reporting_export.py`**
   - Supposed purpose: Test coverage for reporting export.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_export.py`.

165. **`tests/test_template_placeholders.py`**
   - Supposed purpose: Test coverage for template placeholders.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_template_placeholders.py`.

166. **`tests/test_trait_definition_loader.py`**
   - Supposed purpose: Test coverage for trait definition loader.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_trait_definition_loader.py`.

167. **`tests/test_trait_scoring_adapter.py`**
   - Supposed purpose: Test coverage for trait scoring adapter.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_trait_adapter.py`.

168. **`tests/test_trait_signal_schema.py`**
   - Supposed purpose: Test coverage for trait signal schema.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_trait_signal_schema.py`.

169. **`tests/test_trait_signal_state.py`**
   - Supposed purpose: Test coverage for trait signal state.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_scoring_reporting_trait_signal_state.py`.

170. **`tests/test_transcript_accumulator.py`**
   - Supposed purpose: Test coverage for transcript accumulator.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_transcript_accumulator.py`.

171. **`tests/test_transcription_diagnostics.py`**
   - Supposed purpose: Test coverage for transcription diagnostics.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_transcription_diagnostics.py`.

172. **`tests/test_ui_mode_switch.py`**
   - Supposed purpose: Test coverage for ui mode switch.
   - Replacement/status: Removed with obsolete interface/product. Unique behavior coverage was migrated to canonical-owner tests when behavior remained.

173. **`tests/test_ux_metrics.py`**
   - Supposed purpose: Test coverage for ux metrics.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_platform_services_ux_metrics.py`.

174. **`tests/test_web_app_backend.py`**
   - Supposed purpose: Test coverage for web app backend.
   - Replacement/status: Removed with obsolete interface/product. Unique behavior coverage was migrated to canonical-owner tests when behavior remained.

175. **`tests/test_web_app_static.py`**
   - Supposed purpose: Test coverage for web app static.
   - Replacement/status: Removed with obsolete interface/product. Unique behavior coverage was migrated to canonical-owner tests when behavior remained.

176. **`tests/test_web_scored_question_static.py`**
   - Supposed purpose: Test coverage for web scored question static.
   - Replacement/status: Removed with obsolete interface/product. Unique behavior coverage was migrated to canonical-owner tests when behavior remained.

177. **`tests/test_whisper_runtime_policy.py`**
   - Supposed purpose: Test coverage for whisper runtime policy.
   - Replacement/status: Replaced by renamed/migrated file `tests/test_interview_runtime_whisper_runtime_policy.py`.

178. **`web/app/app.js`**
   - Supposed purpose: Frontend asset for unsupported browser interview prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

179. **`web/app/data.js`**
   - Supposed purpose: Frontend asset for unsupported browser interview prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

180. **`web/app/index.html`**
   - Supposed purpose: Frontend asset for unsupported browser interview prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

181. **`web/app/README.md`**
   - Supposed purpose: Frontend asset for unsupported browser interview prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

182. **`web/app/styles.css`**
   - Supposed purpose: Frontend asset for unsupported browser interview prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

183. **`web/scored-question/app.js`**
   - Supposed purpose: Frontend asset for unsupported standalone scored-question prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

184. **`web/scored-question/index.html`**
   - Supposed purpose: Frontend asset for unsupported standalone scored-question prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

185. **`web/scored-question/README.md`**
   - Supposed purpose: Frontend asset for unsupported standalone scored-question prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

186. **`web/scored-question/styles.css`**
   - Supposed purpose: Frontend asset for unsupported standalone scored-question prototype.
   - Replacement/status: Replaced product direction: supported PySide desktop UI. Browser prototype intentionally retired.

## List 2 � Deleted in-file code and UI items

1. **definition: `src/dashboard_v2_ui.py:DashboardV2Shell.semantic_chip`**
   - Supposed purpose: Performed semantic chip operation on dashboard v2 shell.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

2. **definition: `src/data_store.py:QuestionOverridesStore.clear_trait_question_override`**
   - Supposed purpose: Performed clear trait question override operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

3. **definition: `src/data_store.py:QuestionOverridesStore.delete_custom_question`**
   - Supposed purpose: Performed delete custom question operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

4. **definition: `src/data_store.py:QuestionOverridesStore.insert_custom_into_flow`**
   - Supposed purpose: Performed insert custom into flow operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

5. **definition: `src/data_store.py:QuestionOverridesStore.remove_custom_from_flow`**
   - Supposed purpose: Performed remove custom from flow operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

6. **definition: `src/data_store.py:QuestionOverridesStore.set_trait_order`**
   - Supposed purpose: Performed set trait order operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

7. **definition: `src/data_store.py:QuestionOverridesStore.set_trait_question_override`**
   - Supposed purpose: Performed set trait question override operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

8. **definition: `src/data_store.py:QuestionOverridesStore.upsert_custom_question`**
   - Supposed purpose: Performed upsert custom question operation on question overrides store.
   - Replacement/status: Already replaced by canonical owner `platform_services`; deleted copy had no callers.

9. **definition: `src/interview_app/history_actions.py:HistoryActionsService.handle_retranscribe_for_row`**
   - Supposed purpose: Performed handle retranscribe for row operation on history actions service.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

10. **definition: `src/interview_audio_recorder.py:RecordingSession._ensure_not_stopped`**
   - Supposed purpose: Performed ensure not stopped operation on recording session.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

11. **definition: `src/interview_runtime.py:_RuntimeMessageBox.askyesnocancel`**
   - Supposed purpose: Performed askyesnocancel operation on runtime message box.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

12. **definition: `src/interview_runtime.py:AudioRuntimeController.start_recording_with_runtime_fallback`**
   - Supposed purpose: Performed start recording with runtime fallback operation on audio runtime controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

13. **definition: `src/interview_runtime.py:AudioRuntimeController.wait_for_pending_transcriptions`**
   - Supposed purpose: Performed wait for pending transcriptions operation on audio runtime controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

14. **definition: `src/interview_runtime.py:DashboardController.refresh_dashboard`**
   - Supposed purpose: Performed refresh dashboard operation on dashboard controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

15. **definition: `src/interview_runtime.py:FlowController.go_next`**
   - Supposed purpose: Performed go next operation on flow controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

16. **definition: `src/interview_runtime.py:format_runtime_init_error_message`**
   - Supposed purpose: Performed format runtime init error message operation for interview runtime.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

17. **definition: `src/interview_runtime.py:HistoryController.build_history_table`**
   - Supposed purpose: Performed build history table operation on history controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

18. **definition: `src/interview_runtime.py:HistoryController.selected_history_row`**
   - Supposed purpose: Performed selected history row operation on history controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

19. **definition: `src/interview_runtime.py:InterviewSessionManager.load_draft_payload`**
   - Supposed purpose: Performed load draft payload operation on interview session manager.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

20. **definition: `src/interview_runtime.py:TranscriptionQueueState.clear_error`**
   - Supposed purpose: Performed clear error operation on transcription queue state.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

21. **definition: `src/interview_runtime.py:TranscriptionQueueState.is_pending`**
   - Supposed purpose: Performed is pending operation on transcription queue state.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

22. **definition: `src/interview_runtime.py:TranscriptWriterController.append_live_segment`**
   - Supposed purpose: Performed append live segment operation on transcript writer controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

23. **definition: `src/interview_runtime.py:TranscriptWriterController.rewrite_from_flow`**
   - Supposed purpose: Performed rewrite from flow operation on transcript writer controller.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

24. **definition: `src/keyboard_telemetry.py:KeyboardPathSession`**
   - Supposed purpose: Represented keyboard path session behavior/state.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

25. **definition: `src/keyboard_telemetry.py:KeyboardPathSession._is_recent_keyboard_activity`**
   - Supposed purpose: Performed is recent keyboard activity operation on keyboard path session.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

26. **definition: `src/keyboard_telemetry.py:KeyboardPathSession._on_keypress`**
   - Supposed purpose: Performed on keypress operation on keyboard path session.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

27. **definition: `src/keyboard_telemetry.py:KeyboardPathSession.bind`**
   - Supposed purpose: Performed bind operation on keyboard path session.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

28. **definition: `src/keyboard_telemetry.py:KeyboardPathSession.complete`**
   - Supposed purpose: Performed complete operation on keyboard path session.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

29. **definition: `src/keyboard_telemetry.py:KeyboardPathSession.mark_step`**
   - Supposed purpose: Performed mark step operation on keyboard path session.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

30. **definition: `src/notification_models.py:NotificationEvent`**
   - Supposed purpose: Represented notification event behavior/state.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

31. **definition: `src/notification_service.py:_render_notification_template`**
   - Supposed purpose: Performed render notification template operation for notification service.
   - Replacement/status: Already replaced by canonical owner `staffing_service`; deleted copy had no callers.

32. **definition: `src/notification_service.py:emit_notification_event`**
   - Supposed purpose: Performed emit notification event operation for notification service.
   - Replacement/status: Already replaced by canonical owner `staffing_service`; deleted copy had no callers.

33. **definition: `src/onboarding_operations.py:build_specific_date_reference`**
   - Supposed purpose: Performed build specific date reference operation for onboarding operations.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

34. **definition: `src/onboarding_operations.py:format_due_date_short`**
   - Supposed purpose: Performed format due date short operation for onboarding operations.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

35. **definition: `src/onboarding_operations.py:task_status_badge_text`**
   - Supposed purpose: Performed task status badge text operation for onboarding operations.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

36. **definition: `src/onboarding_operations.py:today_local`**
   - Supposed purpose: Performed today local operation for onboarding operations.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

37. **definition: `src/onboarding_operations.py:urgent_filter_result_count`**
   - Supposed purpose: Performed urgent filter result count operation for onboarding operations.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

38. **definition: `src/onboarding_workspace_v2.py:OnboardingDashboardV2Workspace._add_summary`**
   - Supposed purpose: Performed add summary operation on onboarding dashboard v2 workspace.
   - Replacement/status: Already replaced by canonical owner `onboarding_service`; deleted copy had no callers.

39. **definition: `src/platform_services.py:UxMetricsLogger.export_events_csv`**
   - Supposed purpose: Performed export events csv operation on ux metrics logger.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

40. **definition: `src/platform_services.py:UxMetricsLogger.log_keyboard_path_completed`**
   - Supposed purpose: Performed log keyboard path completed operation on ux metrics logger.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

41. **definition: `src/pyside_interview_app.py:_history_outcome_color`**
   - Supposed purpose: Performed history outcome color operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

42. **definition: `src/pyside_interview_app.py:_history_token_matches`**
   - Supposed purpose: Performed history token matches operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

43. **definition: `src/pyside_interview_app.py:_InMemoryQuestionOverridesStore`**
   - Supposed purpose: Represented in memory question overrides store behavior/state.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

44. **definition: `src/pyside_interview_app.py:_InMemoryRubricLoader`**
   - Supposed purpose: Represented in memory rubric loader behavior/state.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

45. **definition: `src/pyside_interview_app.py:_offer_school_code`**
   - Supposed purpose: Performed offer school code operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

46. **definition: `src/pyside_interview_app.py:_offer_school_location`**
   - Supposed purpose: Performed offer school location operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

47. **definition: `src/pyside_interview_app.py:_parse_iso_or_us_date`**
   - Supposed purpose: Performed parse iso or us date operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

48. **definition: `src/pyside_interview_app.py:_safe_filename`**
   - Supposed purpose: Performed safe filename operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

49. **definition: `src/pyside_interview_app.py:_table_text`**
   - Supposed purpose: Performed table text operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

50. **definition: `src/pyside_interview_app.py:director_offer_shift_for_history`**
   - Supposed purpose: Performed director offer shift for history operation for pyside interview app.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

51. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._close_pyside_finalize_progress`**
   - Supposed purpose: Performed close pyside finalize progress operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

52. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._horizontal_scroll_panel`**
   - Supposed purpose: Performed horizontal scroll panel operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

53. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._onboarding_page`**
   - Supposed purpose: Performed onboarding page operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

54. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._placeholder_page`**
   - Supposed purpose: Performed placeholder page operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

55. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._populate_home_candidate_profile`**
   - Supposed purpose: Performed populate home candidate profile operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

56. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._preload_recording_interface`**
   - Supposed purpose: Performed preload recording interface operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

57. **definition: `src/pyside_interview_app.py:PySideInterviewWindow._setup_tab`**
   - Supposed purpose: Performed setup tab operation on py side interview window.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

58. **definition: `src/question_runtime_definition_service.py:normalize_runtime_group`**
   - Supposed purpose: Performed normalize runtime group operation for question runtime definition service.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

59. **definition: `src/question_runtime_definition_service.py:normalize_runtime_signal`**
   - Supposed purpose: Performed normalize runtime signal operation for question runtime definition service.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

60. **definition: `src/question_runtime_definition_service.py:QuestionRuntimeDefinitionService.delete_extended_group`**
   - Supposed purpose: Performed delete extended group operation on question runtime definition service.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

61. **definition: `src/question_runtime_definition_service.py:QuestionRuntimeDefinitionService.sync_with_trait`**
   - Supposed purpose: Performed sync with trait operation on question runtime definition service.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

62. **definition: `src/question_runtime_definition_service.py:QuestionRuntimeDefinitionService.update_core_signal`**
   - Supposed purpose: Performed update core signal operation on question runtime definition service.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

63. **definition: `src/question_runtime_definition_service.py:QuestionRuntimeDefinitionService.update_extended_group`**
   - Supposed purpose: Performed update extended group operation on question runtime definition service.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

64. **definition: `src/question_settings_service.py:QuestionSettingsService.export_questions`**
   - Supposed purpose: Performed export questions operation on question settings service.
   - Replacement/status: Already replaced by canonical owner `question_runtime_definition_service`; deleted copy had no callers.

65. **definition: `src/question_settings_service.py:QuestionSettingsService.import_questions`**
   - Supposed purpose: Performed import questions operation on question settings service.
   - Replacement/status: Already replaced by canonical owner `question_runtime_definition_service`; deleted copy had no callers.

66. **definition: `src/scoring_reporting.py:build_offer_filename`**
   - Supposed purpose: Performed build offer filename operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

67. **definition: `src/scoring_reporting.py:default_referral_endpoint`**
   - Supposed purpose: Performed default referral endpoint operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

68. **definition: `src/scoring_reporting.py:insert_token_into_focused_widget`**
   - Supposed purpose: Performed insert token into focused widget operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

69. **definition: `src/scoring_reporting.py:OfferLetterService.classify_employment_type`**
   - Supposed purpose: Performed classify employment type operation on offer letter service.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

70. **definition: `src/scoring_reporting.py:open_outlook_draft`**
   - Supposed purpose: Performed open outlook draft operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

71. **definition: `src/scoring_reporting.py:placeholder_picker_options`**
   - Supposed purpose: Performed placeholder picker options operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

72. **definition: `src/scoring_reporting.py:placeholder_tokens_for_context`**
   - Supposed purpose: Performed placeholder tokens for context operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

73. **definition: `src/scoring_reporting.py:sender_email_domain_type`**
   - Supposed purpose: Performed sender email domain type operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

74. **definition: `src/scoring_reporting.py:token_from_picker_label`**
   - Supposed purpose: Performed token from picker label operation for scoring reporting.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

75. **definition: `src/staffing_dashboard_v2.py:_format_notification_recipients`**
   - Supposed purpose: Performed format notification recipients operation for staffing dashboard v2.
   - Replacement/status: Already replaced by canonical owner `dashboard_v2_ui`; deleted copy had no callers.

76. **definition: `src/staffing_dashboard_v2.py:_status_color`**
   - Supposed purpose: Performed status color operation for staffing dashboard v2.
   - Replacement/status: Already replaced by canonical owner `dashboard_v2_ui`; deleted copy had no callers.

77. **definition: `src/staffing_dashboard_v2.py:StaffingDashboardV2Page._position_classrooms_filter_drawer`**
   - Supposed purpose: Performed position classrooms filter drawer operation on staffing dashboard v2 page.
   - Replacement/status: Already replaced by canonical owner `dashboard_v2_ui`; deleted copy had no callers.

78. **definition: `src/ui_feedback.py:append_error_log`**
   - Supposed purpose: Performed append error log operation for ui feedback.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

79. **definition: `src/ui_feedback.py:InlineValidationMessage`**
   - Supposed purpose: Represented inline validation message behavior/state.
   - Replacement/status: No replacement. Recheck found no direct call, import call, dynamic lookup, callback, hook, override, serialization use, launcher dependency, or locked contract requirement.

80. **import: `src/hiring_workspace_v2.py:SEMANTIC_COLORS`**
   - Supposed purpose: Made `SEMANTIC_COLORS` available to `src/hiring_workspace_v2.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `hiring_pipeline`; deleted copy had no callers.

81. **import: `src/notification_service.py:NOTIFICATION_TEMPLATE_FIELDS`**
   - Supposed purpose: Made `NOTIFICATION_TEMPLATE_FIELDS` available to `src/notification_service.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `staffing_service`; deleted copy had no callers.

82. **import: `src/pyside_interview_app.py:build_offer_filename`**
   - Supposed purpose: Made `build_offer_filename` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

83. **import: `src/pyside_interview_app.py:Callable`**
   - Supposed purpose: Made `Callable` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

84. **import: `src/pyside_interview_app.py:CompletedInterviewViewModel`**
   - Supposed purpose: Made `CompletedInterviewViewModel` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

85. **import: `src/pyside_interview_app.py:default_school_offer_settings`**
   - Supposed purpose: Made `default_school_offer_settings` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

86. **import: `src/pyside_interview_app.py:Document`**
   - Supposed purpose: Made `Document` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

87. **import: `src/pyside_interview_app.py:Formatter`**
   - Supposed purpose: Made `Formatter` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

88. **import: `src/pyside_interview_app.py:notification_service_from_onboarding`**
   - Supposed purpose: Made `notification_service_from_onboarding` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

89. **import: `src/pyside_interview_app.py:POSITION_OPTIONS`**
   - Supposed purpose: Made `POSITION_OPTIONS` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

90. **import: `src/pyside_interview_app.py:timedelta`**
   - Supposed purpose: Made `timedelta` available to `src/pyside_interview_app.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

91. **import: `src/staffing_dashboard_v2.py:NOTIFICATION_TEMPLATE_FIELDS`**
   - Supposed purpose: Made `NOTIFICATION_TEMPLATE_FIELDS` available to `src/staffing_dashboard_v2.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `dashboard_v2_ui`; deleted copy had no callers.

92. **import: `tests/test_admin_studio.py:pytest`**
   - Supposed purpose: Made `pytest` available to `tests/test_admin_studio.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `admin_studio`; deleted copy had no callers.

93. **import: `tests/test_app_logging.py:Path`**
   - Supposed purpose: Made `Path` available to `tests/test_app_logging.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `app_logging`; deleted copy had no callers.

94. **import: `tests/test_check_contract_review.py:pytest`**
   - Supposed purpose: Made `pytest` available to `tests/test_check_contract_review.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `tools.check_contract_review`; deleted copy had no callers.

95. **import: `tests/test_cross_database_change_stage.py:pytest`**
   - Supposed purpose: Made `pytest` available to `tests/test_cross_database_change_stage.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `cross_database_change_stage`; deleted copy had no callers.

96. **import: `tests/test_interview_app_controllers.py:InterviewHistoryStore`**
   - Supposed purpose: Made `InterviewHistoryStore` available to `tests/test_interview_app_controllers.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

97. **import: `tests/test_interview_app_controllers.py:json`**
   - Supposed purpose: Made `json` available to `tests/test_interview_app_controllers.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `interview_runtime`; deleted copy had no callers.

98. **import: `tests/test_onboarding_package_editor.py:Path`**
   - Supposed purpose: Made `Path` available to `tests/test_onboarding_package_editor.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `onboarding_package_editor`; deleted copy had no callers.

99. **import: `tests/test_scoring_engine.py:ReportingValidationError`**
   - Supposed purpose: Made `ReportingValidationError` available to `tests/test_scoring_engine.py` for intended local use.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

100. **import: `tests/test_trait_based_scoring_engine_regression.py:StringIO`**
   - Supposed purpose: Made `StringIO` available to `tests/test_trait_based_scoring_engine_regression.py` for intended local use.
   - Replacement/status: No replacement needed. Imported binding had no references; removing it changes no behavior.

101. **import: `tools/generate_director_staffing_launchers.py:Any`**
   - Supposed purpose: Made `Any` available to `tools/generate_director_staffing_launchers.py` for intended local use.
   - Replacement/status: No replacement needed. Imported binding had no references; removing it changes no behavior.

102. **constant: `src/interview_runtime.py:_DEFAULT_MISSING_SUMMARY`**
   - Supposed purpose: Stored shared value/config named `_DEFAULT_MISSING_SUMMARY` for interview runtime behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

103. **constant: `src/interview_runtime.py:_SUMMARIZATION_PREFIX`**
   - Supposed purpose: Stored shared value/config named `_SUMMARIZATION_PREFIX` for interview runtime behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

104. **constant: `src/interview_runtime.py:_SUMMARY_TASK`**
   - Supposed purpose: Stored shared value/config named `_SUMMARY_TASK` for interview runtime behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

105. **constant: `src/interview_runtime.py:_TEXT2TEXT_TASK`**
   - Supposed purpose: Stored shared value/config named `_TEXT2TEXT_TASK` for interview runtime behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

106. **constant: `src/interview_runtime.py:_UNKNOWN_TASK_MARKER`**
   - Supposed purpose: Stored shared value/config named `_UNKNOWN_TASK_MARKER` for interview runtime behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

107. **constant: `src/notification_service.py:SUPPORTED_NOTIFICATION_EVENTS`**
   - Supposed purpose: Stored shared value/config named `SUPPORTED_NOTIFICATION_EVENTS` for notification service behavior.
   - Replacement/status: Already replaced by canonical owner `staffing_service`; deleted copy had no callers.

108. **constant: `src/question_runtime_definition_service.py:BSS_TRAIT_ID_ALIAS_PATTERN`**
   - Supposed purpose: Stored shared value/config named `BSS_TRAIT_ID_ALIAS_PATTERN` for question runtime definition service behavior.
   - Replacement/status: Already replaced by canonical owner `scoring_reporting`; deleted copy had no callers.

109. **constant: `src/scoring_reporting.py:_EXECUTIVE_SUMMARY_HEADINGS`**
   - Supposed purpose: Stored shared value/config named `_EXECUTIVE_SUMMARY_HEADINGS` for scoring reporting behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

110. **constant: `src/scoring_reporting.py:_EXECUTIVE_SUMMARY_LIST_SECTIONS`**
   - Supposed purpose: Stored shared value/config named `_EXECUTIVE_SUMMARY_LIST_SECTIONS` for scoring reporting behavior.
   - Replacement/status: No replacement needed. Constant had zero references after protected/dynamic names were excluded.

111. **UI route/control: `Analytics navigation placeholder`**
   - Supposed purpose: Displayed permanently disabled Analytics navigation placeholder.
   - Replacement/status: No replacement. No page, signal, enable path, or supported product purpose existed.

112. **UI route/control: `Dashboard navigation placeholder`**
   - Supposed purpose: Displayed permanently disabled Staffing Dashboard navigation placeholder.
   - Replacement/status: Already replaced by active Staffing Dashboard route; disabled duplicate placeholder removed.

113. **UI route/control: `Integrations navigation placeholder`**
   - Supposed purpose: Displayed permanently disabled Integrations navigation placeholder.
   - Replacement/status: No replacement. No page, signal, enable path, or supported product purpose existed.

114. **UI route/control: `legacy Setup-tab Begin Interview button`**
   - Supposed purpose: Displayed legacy Begin Interview button inside hidden Setup tab.
   - Replacement/status: Already replaced by active `HiringV2SetupBegin` action on Home tab.

115. **UI route/control: `PySideInterviewWindow._onboarding_page legacy v1 board`**
   - Supposed purpose: Built legacy onboarding-v1 page and retained its window field/imports.
   - Replacement/status: Already replaced by `StaffingDashboardHost` and five active onboarding-v2 workspace routes.

116. **UI route/control: `PySideInterviewWindow._setup_tab / interview_tabs index 1`**
   - Supposed purpose: Built hidden legacy Setup tab and inserted it at interview-tab index 1.
   - Replacement/status: Already replaced by Home/Live/Review PySide routes using named tab-index constants.
