/*
Copyright 2025.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package controller

import (
	"context"

	"github.com/go-logr/logr"                     // Explicitly import logr
	"k8s.io/apimachinery/pkg/api/meta"            // For meta.SetStatusCondition
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1" // For metav1.Condition
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	kubedevaiopsv1alpha1 "github.com/kubedevaiops/kubedevaiops-operator/api/v1alpha1"
)

// AIRequestReconciler reconciles a AIRequest object
type AIRequestReconciler struct {
	client.Client
	Scheme *runtime.Scheme
	Log    logr.Logger // Use logr.Logger explicitly
}

//+kubebuilder:rbac:groups=kubedevaiops.kubedevaiops.com,resources=airequests,verbs=get;list;watch;create;update;patch;delete
//+kubebuilder:rbac:groups=kubedevaiops.kubedevaiops.com,resources=airequests/status,verbs=get;update;patch
//+kubebuilder:rbac:groups=kubedevaiops.kubedevaiops.com,resources=airequests/finalizers,verbs=update

const (
	ConditionTypeProcessing       string = "Processing"
	ConditionTypeAwaitingApproval string = "AwaitingApproval"
	ConditionTypeReady            string = "Ready" // Though 'Ready' might map to 'Completed' or 'Failed' later
)

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
// TODO(user): Modify the Reconcile function to compare the state specified by
// the AIRequest object against the actual cluster state, and then
// perform operations to make the cluster state reflect the state specified by
// the user.
//
// For more details, check Reconcile and its Result here:
// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.15.0/pkg/reconcile
func (r *AIRequestReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	// log := r.Log.WithValues("airequest", req.NamespacedName) // Use r.Log - this line will be updated by the user if needed or kept as is. For now, use controller-runtime's log package directly for safety.
	log := ctrl.Log.WithName("controllers").WithName("AIRequest").WithValues("airequest", req.NamespacedName)
	log.Info("Reconciling AIRequest")

	aiRequest := &kubedevaiopsv1alpha1.AIRequest{}
	if err := r.Get(ctx, req.NamespacedName, aiRequest); err != nil {
		log.Error(err, "unable to fetch AIRequest")
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	// Initialize status if it's the first time
	if aiRequest.Status.Conditions == nil {
		aiRequest.Status.Conditions = []metav1.Condition{}
	}

	// Set observedPrompt
	aiRequest.Status.ObservedPrompt = aiRequest.Spec.Prompt

	// Initial condition: Processing
	processingCondition := metav1.Condition{
		Type:    ConditionTypeProcessing,
		Status:  metav1.ConditionTrue,
		Reason:  "ProcessingPrompt",
		Message: "The user prompt is being processed by the AI.",
	}
	meta.SetStatusCondition(&aiRequest.Status.Conditions, processingCondition)

	// Update status after setting initial condition (important before long operations)
	// It's good practice to update status early if possible.
	// However, for this stub, we'll do one update at the end.

	// ** BEGIN AI Interaction Placeholder **
	log.Info("Simulating AI processing for prompt", "prompt", aiRequest.Spec.Prompt)
	// In a real scenario, this is where you'd call the AI.
	// For now, set dummy proposed manifests and explanation.
	dummyManifest := `apiVersion: v1
kind: ConfigMap
metadata:
  name: dummy-config-` + aiRequest.Name + `
  namespace: ` + aiRequest.Namespace + `
data:
  message: "This is a dummy manifest generated for prompt: ` + aiRequest.Spec.Prompt + `"
`
	aiRequest.Status.ProposedManifests = dummyManifest
	aiRequest.Status.AIExplanation = "This is a placeholder explanation. In the future, an AI will generate this along with the manifests based on your prompt."
	// ** END AI Interaction Placeholder **

	// Update condition: AwaitingApproval (after AI processing placeholder)
	meta.RemoveStatusCondition(&aiRequest.Status.Conditions, ConditionTypeProcessing) // Remove processing
	awaitingApprovalCondition := metav1.Condition{
		Type:    ConditionTypeAwaitingApproval,
		Status:  metav1.ConditionTrue,
		Reason:  "AwaitingUserApproval",
		Message: "AI has processed the prompt and proposed manifests are awaiting user approval.",
	}
	meta.SetStatusCondition(&aiRequest.Status.Conditions, awaitingApprovalCondition)

	// Attempt to update the status
	if err := r.Status().Update(ctx, aiRequest); err != nil {
		log.Error(err, "unable to update AIRequest status")
		return ctrl.Result{}, err
	}

	log.Info("Successfully reconciled AIRequest and set to AwaitingApproval", "Name", req.Name)
	return ctrl.Result{}, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *AIRequestReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&kubedevaiopsv1alpha1.AIRequest{}).
		Complete(r)
}
