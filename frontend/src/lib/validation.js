import { validateImageFile } from "./api";
import { CATEGORIES } from "./lifecycle";

export const LIMITS = { title: 150, location: 250, description: 3000, landmark: 150 };

/** Mirrors backend validation so citizens see errors before submitting. */
export function validateComplaintForm(form) {
  const errors = {};
  const title = (form.title || "").trim();
  const location = (form.location || "").trim();
  const description = (form.description || "").trim();
  if (!title) errors.title = "Give the issue a short title.";
  else if (title.length < 5) errors.title = "Title should be at least 5 characters.";
  else if (title.length > LIMITS.title) errors.title = `Title must be ${LIMITS.title} characters or fewer.`;
  if (!CATEGORIES.includes(form.category)) errors.category = "Choose a category.";
  if (!location) errors.location = "Enter the address, area or nearest landmark.";
  else if (location.length > LIMITS.location) errors.location = `Location must be ${LIMITS.location} characters or fewer.`;
  if (!description) errors.description = "Describe the issue.";
  else if (description.length < 10) errors.description = "Please add a little more detail (10+ characters).";
  else if (description.length > LIMITS.description) errors.description = `Description must be ${LIMITS.description} characters or fewer.`;
  if ((form.landmark || "").length > LIMITS.landmark) errors.landmark = `Landmark must be ${LIMITS.landmark} characters or fewer.`;
  if (form.photo) {
    const photoError = validateImageFile(form.photo);
    if (photoError) errors.photo = photoError;
  }
  if (form.coords) {
    const { latitude, longitude } = form.coords;
    if (!(latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180)) errors.coords = "Invalid map location.";
  }
  return errors;
}

export function buildComplaintPayload(form, photoKey, clientRequestId) {
  return {
    title: form.title.trim(),
    category: form.category,
    location: form.location.trim(),
    description: form.description.trim(),
    ...(form.landmark?.trim() ? { landmark: form.landmark.trim() } : {}),
    ...(form.coords ? {
      latitude: Number(form.coords.latitude.toFixed(6)),
      longitude: Number(form.coords.longitude.toFixed(6)),
      ...(form.coords.accuracy ? { locationAccuracy: Math.round(form.coords.accuracy) } : {}),
    } : {}),
    ...(photoKey ? { photoKey } : {}),
    ...(clientRequestId ? { clientRequestId } : {}),
  };
}
