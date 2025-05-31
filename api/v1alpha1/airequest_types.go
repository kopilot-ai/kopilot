package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// AIRequestSpec defines the desired state of AIRequest
type AIRequestSpec struct {
	// Prompt is the natural language request from the user.
	// +kubebuilder:validation:MinLength=1
	Prompt string `json:"prompt"`
}

// AIRequestStatus defines the observed state of AIRequest
type AIRequestStatus struct {
	// ObservedPrompt is the last prompt that was observed by the controller.
	ObservedPrompt string `json:"observedPrompt,omitempty"`

	// ProposedManifests contains the Kubernetes manifests proposed by the AI, in YAML format.
	// +kubebuilder:validation:Optional
	ProposedManifests string `json:"proposedManifests,omitempty"`

	// AIExplanation provides an explanation from the AI about the proposed changes or actions.
	// +kubebuilder:validation:Optional
	AIExplanation string `json:"aiExplanation,omitempty"`

	// Conditions represent the latest available observations of an AIRequest's state.
	// +operator-sdk:csv:customresourcedefinitions:type=status
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Prompt",type="string",JSONPath=".spec.prompt"
// +kubebuilder:printcolumn:name="Status",type="string",JSONPath=".status.conditions[?(@.type=='Ready')].reason"
// +kubebuilder:printcolumn:name="Age",type="date",JSONPath=".metadata.creationTimestamp"
// AIRequest is the Schema for the airequests API
type AIRequest struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   AIRequestSpec   `json:"spec,omitempty"`
	Status AIRequestStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true
// AIRequestList contains a list of AIRequest
type AIRequestList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []AIRequest `json:"items"`
}

func init() {
	SchemeBuilder.Register(&AIRequest{}, &AIRequestList{})
}
