export function $(selector, root = document) {
  return root.querySelector(selector);
}

export function on(selector, eventName, handler, root = document) {
  const element = $(selector, root);
  if (element) element.addEventListener(eventName, handler);
  return element;
}
