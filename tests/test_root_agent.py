"""Deterministic Root contracts and dispatch tests; no live services required."""

from contextlib import ExitStack
from copy import deepcopy
import importlib
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from agents import root_agent
from entity.business_result import BusinessResult, BusinessResultReason, BusinessResultStatus
from entity.classification import ClassifierResult, MessageCategory
from entity.conversation import ConversationMessage
from entity.order_creation_state import OrderCreationStage, OrderCreationState, OrderWorkflowStatus
from entity.routing import BusinessAction, RoutingReason, RoutingResult, RoutingStatus, TargetAgent
from agents.root_agent import execute_route, route_message


def classification(category=MessageCategory.CREATE_ORDER, confidence=0.9):
    return ClassifierResult(category=category, confidence=confidence, explanation="Test classification")


def business_result(state, status=BusinessResultStatus.NEEDS_USER_INPUT):
    return BusinessResult(
        workflow_id=state.workflow_id, source_agent="ORDER_AGENT", action="CREATE_ORDER",
        result_status=status, current_stage="COLLECT_REQUIREMENTS",
        reason=BusinessResultReason.MISSING_REQUIRED_INFORMATION,
        data={"example": ["unchanged"]}, required_input=["felt_color"],
    )


class RootRoutingTests(unittest.TestCase):
    def test_exact_nine_direct_category_mappings(self):
        cases = [
            ("GENERAL_ENQUIRY", "SUPPORT_AGENT", "ANSWER_ENQUIRY", None),
            ("CASUAL_CHAT", "SUPPORT_AGENT", "RESPOND_CHAT", None),
            ("SUPPORT_TICKET_FOLLOWUP", "SUPPORT_AGENT", "FOLLOW_UP_SUPPORT_TICKET", None),
            ("UNKNOWN_OTHER_INQUIRY", "SUPPORT_AGENT", "REQUEST_CLARIFICATION", None),
            ("CREATE_ORDER", "ORDER_AGENT", "CREATE_ORDER", "ORDER_CREATE_WF"),
            ("UPDATE_ORDER", "ORDER_AGENT", "UPDATE_ORDER", "ORDER_UPDATE_WF"),
            ("ORDER_ENQUIRY", "ORDER_AGENT", "GET_ORDER_INFO", None),
            ("QUOTATION_ENQUIRY", "ORDER_AGENT", "CREATE_QUOTATION", "QUOTATION_CREATE_WF"),
            ("PRODUCTION_STATUS_ENQUIRY", "PRODUCTION_AGENT", "GET_PRODUCTION_INFO", None),
        ]
        for category, agent, action, workflow in cases:
            with self.subTest(category=category):
                result = route_message(classification(MessageCategory(category)))
                self.assertEqual(result.model_dump(mode="json"), {
                    "source_category": category, "target_agent": agent, "business_action": action,
                    "workflow_type": workflow,
                    "status": "READY" if category == "CREATE_ORDER" or agent == "SUPPORT_AGENT" else "UNAVAILABLE",
                    "reason": "ROUTE_AVAILABLE" if category == "CREATE_ORDER" or agent == "SUPPORT_AGENT" else "DOWNSTREAM_NOT_IMPLEMENTED",
                })

    def test_workflow_response_routes_by_active_identity(self):
        for status in (OrderWorkflowStatus.ACTIVE, OrderWorkflowStatus.AWAITING_USER_INPUT):
            with self.subTest(status=status):
                state = OrderCreationState(conversation_id="test", status=status)
                route = route_message(classification(MessageCategory.WORKFLOW_RESPONSE), state)
                self.assertEqual(route.target_agent, TargetAgent.ORDER_AGENT)
                self.assertEqual(route.business_action, BusinessAction.CREATE_ORDER)
                self.assertEqual(route.workflow_type, "ORDER_CREATE_WF")
                self.assertEqual(route.status, RoutingStatus.READY)

    def test_missing_and_terminal_workflows_are_unresolved(self):
        states = [None] + [OrderCreationState(conversation_id="test", status=status) for status in
                           (OrderWorkflowStatus.COMPLETED, OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED)]
        for state in states:
            with self.subTest(state=state):
                route = route_message(classification(MessageCategory.WORKFLOW_RESPONSE), state)
                self.assertEqual(route.status, RoutingStatus.UNRESOLVED)
                self.assertEqual(route.reason, RoutingReason.NO_ACTIVE_WORKFLOW if state is None
                                 else RoutingReason.WORKFLOW_NOT_ACTIVE)
                self.assertIsNone(route.target_agent)
                self.assertIsNone(route.business_action)

    def test_routing_ignores_business_evidence_and_does_not_mutate_inputs(self):
        state = OrderCreationState(conversation_id="test")
        original = route_message(classification(MessageCategory.WORKFLOW_RESPONSE), state)
        for stage in OrderCreationStage:
            state.current_stage = stage
            state.pending_field = "felt_color"
            state.configuration_confirmed = True
            state.final_order_confirmed = True
            state.order_snapshot = {"unrelated": "evidence"}
            state.total_price = 123
            before = deepcopy(state)
            for confidence in (0.0, 1.0):
                item = classification(MessageCategory.WORKFLOW_RESPONSE, confidence)
                before_item = deepcopy(item)
                self.assertEqual(route_message(item, state), original)
                self.assertEqual(state, before)
                self.assertEqual(item, before_item)
        self.assertNotIn("action", ClassifierResult.model_fields)

    def test_routing_never_calls_llm_agents_controller_or_store(self):
        targets = ["workflow.classifier.chat", "workflow.order.order_creation_extraction.chat",
                   "workflow.order.order_creation_confirmation.chat", "agents.root_agent.process_order_creation_message",
                   "workflow.order.order_creation_controller.execute_order_creation_workflow",
                   "workflow.order.order_creation_controller.apply_order_creation_reentry",
                   "workflow.order.order_creation_order_store.create_order"]
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(target)) for target in targets]
            for category in MessageCategory:
                route_message(classification(category), OrderCreationState(conversation_id="test"))
            for mock in mocks:
                mock.assert_not_called()

    def test_invalid_inputs_fail_explicitly(self):
        for item in (None, "CREATE_ORDER", {"category": "CREATE_ORDER"}):
            with self.subTest(item=item), self.assertRaises(TypeError):
                route_message(item)
        bad = classification()
        bad.category = "unexpected"
        with self.assertRaises(ValidationError):
            route_message(bad)
        for state in ({}, "active", object()):
            with self.assertRaises(TypeError):
                route_message(classification(), state)
        for field, value in (("owner_agent", "SUPPORT_AGENT"), ("workflow_type", "OTHER_WF"),
                             ("status", "unknown")):
            state = OrderCreationState(conversation_id="test")
            setattr(state, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                route_message(classification(), state)

    def test_routing_schema_is_strict_and_immutable(self):
        route = route_message(classification())
        self.assertFalse(RoutingResult.model_json_schema()["additionalProperties"])
        with self.assertRaises(ValidationError):
            route.status = RoutingStatus.UNAVAILABLE
        with self.assertRaises(ValidationError):
            RoutingResult.model_validate({**route.model_dump(), "executed": True})
        self.assertEqual(RoutingResult.model_validate_json(route.model_dump_json()), route)

    def test_import_has_no_demo_output_or_calls(self):
        with patch("builtins.print") as output, patch("workflow.classifier.chat") as llm:
            # A fresh execution of the module without replacing classes used by other tests.
            spec = importlib.util.spec_from_file_location("root_import_test", root_agent.__file__)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output.assert_not_called()
            llm.assert_not_called()


class RootExecutionTests(unittest.TestCase):
    def setUp(self):
        self.state = OrderCreationState(conversation_id="test")
        self.history = [ConversationMessage(role="assistant", content="What cloth colour would you like?")]
        self.processor = Mock()
        self.updated = self.state.model_copy(deep=True)
        self.result = business_result(self.updated)
        self.processor.return_value = (self.updated, self.result)

    def execute(self, route, state=None, **kwargs):
        args = dict(current_message="Blue.", conversation_history=self.history,
                    conversation_id="test", state=state, order_creation_processor=self.processor)
        args.update(kwargs)
        return execute_route(route, **args)

    def test_create_order_bootstraps_defaults_only_before_dispatch(self):
        def processor(message, history, state):
            defaults = OrderCreationState(conversation_id="test")
            generated = {"workflow_id", "created_at", "updated_at"}
            self.assertEqual(state.model_dump(exclude=generated), defaults.model_dump(exclude=generated))
            self.assertEqual(state.current_stage, OrderCreationStage.COLLECT_REQUIREMENTS)
            self.assertIsNone(state.pending_field)
            return state, business_result(state)
        self.processor.side_effect = processor
        route = route_message(classification())
        outcome = self.execute(route)
        self.assertTrue(outcome.executed)
        self.processor.assert_called_once()
        self.assertIs(outcome.state, self.processor.call_args.args[2])

    def test_existing_state_reused_for_create_and_workflow_response(self):
        for category in (MessageCategory.CREATE_ORDER, MessageCategory.WORKFLOW_RESPONSE):
            with self.subTest(category=category):
                self.processor.reset_mock()
                before = deepcopy((self.state, self.history))
                outcome = self.execute(route_message(classification(category), self.state), self.state)
                self.processor.assert_called_once()
                message, history, state = self.processor.call_args.args
                self.assertEqual(message, "Blue.")
                self.assertEqual(history, self.history)
                self.assertIs(state, self.state)
                self.assertEqual((self.state, self.history), before)
                self.assertIs(outcome.state, self.updated)
                self.assertIs(outcome.business_result, self.result)

    def test_explicit_create_passes_terminal_state_without_recovery(self):
        for status in (OrderWorkflowStatus.COMPLETED, OrderWorkflowStatus.FAILED, OrderWorkflowStatus.CANCELLED):
            self.state.status = status
            self.execute(route_message(classification(), self.state), self.state)
            self.assertIs(self.processor.call_args.args[2], self.state)
            self.assertEqual(self.state.status, status)

    def test_executed_means_normal_return_for_every_business_status(self):
        for status in BusinessResultStatus:
            result = business_result(self.updated, status)
            self.processor.return_value = (self.updated, result)
            outcome = self.execute(route_message(classification(), self.state), self.state)
            self.assertTrue(outcome.executed)
            self.assertIs(outcome.business_result, result)
            self.assertEqual(outcome.business_result.result_status, status)

    def test_unavailable_and_unresolved_routes_never_dispatch_or_bootstrap(self):
        for category in MessageCategory:
            if category in (MessageCategory.CREATE_ORDER, MessageCategory.GENERAL_ENQUIRY, MessageCategory.CASUAL_CHAT,
                            MessageCategory.SUPPORT_TICKET_FOLLOWUP, MessageCategory.UNKNOWN_OTHER_INQUIRY):
                continue
            state = None if category == MessageCategory.WORKFLOW_RESPONSE else self.state
            route = route_message(classification(category), state)
            with self.subTest(category=category):
                outcome = self.execute(route, state)
                self.assertFalse(outcome.executed)
                self.assertIs(outcome.state, state)
                self.assertIsNone(outcome.business_result)
        self.processor.assert_not_called()

    def test_terminal_workflow_response_does_not_execute(self):
        self.state.status = OrderWorkflowStatus.COMPLETED
        route = route_message(classification(MessageCategory.WORKFLOW_RESPONSE), self.state)
        self.assertFalse(self.execute(route, self.state).executed)
        self.processor.assert_not_called()

    def test_invalid_or_stale_routes_rejected(self):
        route = route_message(classification(), self.state)
        for bad in (None, {}, "CREATE_ORDER"):
            with self.assertRaises(TypeError):
                self.execute(bad, self.state)
        for changes in ({"business_action": BusinessAction.UPDATE_ORDER},
                        {"target_agent": TargetAgent.SUPPORT_AGENT},
                        {"status": RoutingStatus.UNAVAILABLE},
                        {"workflow_type": "OTHER_WF"}):
            with self.assertRaises(ValueError):
                self.execute(route.model_copy(update=changes), self.state)
        active_route = route_message(classification(MessageCategory.WORKFLOW_RESPONSE), self.state)
        self.state.status = OrderWorkflowStatus.COMPLETED
        with self.assertRaises(ValueError):
            self.execute(active_route, self.state)
        with self.assertRaises(ValueError):
            self.execute(active_route)
        self.processor.assert_not_called()

    def test_invalid_dispatch_inputs_and_conversation_mismatch(self):
        route = route_message(classification(), self.state)
        for kwargs in ({"current_message": " "}, {"current_message": 1},
                       {"conversation_id": ""}, {"conversation_id": "different"},
                       {"conversation_history": "legacy string"},
                       {"conversation_history": [{"role": "system", "content": "invalid"}]},
                       {"order_creation_processor": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises((ValueError, TypeError)):
                self.execute(route, self.state, **kwargs)
        self.processor.assert_not_called()

    def test_history_dictionaries_use_shared_contract(self):
        history = [{"role": "user", "content": "I would like a table."}]
        self.execute(route_message(classification(), self.state), self.state, conversation_history=history)
        received = self.processor.call_args.args[1]
        self.assertIsInstance(received[0], ConversationMessage)
        self.assertEqual(received[0].model_dump(), history[0])

    def test_processor_failure_propagates_without_retries_or_state_changes(self):
        error = ConnectionError("Backend unavailable")
        self.processor.side_effect = error
        before = deepcopy(self.state)
        with self.assertRaises(ConnectionError) as caught:
            self.execute(route_message(classification(), self.state), self.state)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.state, before)
        self.processor.assert_called_once()

    def test_invalid_processor_return_is_not_a_normal_execution_result(self):
        for result in ((None, None), (self.updated, {}), ({}, self.result)):
            self.processor.return_value = result
            with self.subTest(result=result), self.assertRaises(TypeError):
                self.execute(route_message(classification(), self.state), self.state)

    def test_support_dispatch_has_separate_outcome_and_preserves_workflow(self):
        from entity.support import CustomerResponse, SupportAction, SupportActionResult, SupportOutcome
        support_result = SupportActionResult(action=SupportAction.RESPOND_CHAT,
            outcome=SupportOutcome.ANSWERED, response=CustomerResponse(text="Hello!"))
        support = Mock(return_value=support_result)
        route = route_message(classification(MessageCategory.CASUAL_CHAT), self.state)
        before = deepcopy(self.state)
        result = self.execute(route, self.state, support_processor=support)
        self.assertTrue(result.executed)
        self.assertEqual(result.support_result, support_result)
        self.assertIsNone(result.business_result)
        self.assertIs(result.state, self.state)
        self.assertEqual(self.state, before)
        support.assert_called_once_with(SupportAction.RESPOND_CHAT, "Blue.",
                                        conversation_history=self.history, business_context=None)
        self.processor.assert_not_called()

    def test_support_dispatch_rejects_wrong_action_result(self):
        from entity.support import CustomerResponse, SupportAction, SupportActionResult, SupportOutcome
        wrong = SupportActionResult(action=SupportAction.ANSWER_ENQUIRY,
            outcome=SupportOutcome.ANSWERED, response=CustomerResponse(text="Hello!"))
        route = route_message(classification(MessageCategory.CASUAL_CHAT), self.state)
        with self.assertRaises(TypeError):
            self.execute(route, self.state, support_processor=Mock(return_value=wrong))
        self.processor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
