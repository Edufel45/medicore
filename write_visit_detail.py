# -*- coding: utf-8 -*-
import os

path = os.path.join("templates", "clinical", "visit_detail.html")

CHUNK_4 = r'''
        {% if user_role == "lab" or user_role == "admin" %}
          <div class="panel">
            <div class="panel-title">Laboratory{% if visit.stage == "lab" %} - you are here{% endif %}</div>

            {% if lab_tests %}
              {% for lt in lab_tests %}
                <div class="row">
                  <div class="l">
                    <b>{{ lt.name }}</b>
                    <span>{% if lt.status == "released" %}Result released to doctor{% else %}Awaiting result entry{% endif %}</span>
                  </div>
                  <span class="badge {% if lt.status == 'released' %}b-active{% else %}b-pending{% endif %}">
                    {% if lt.status == "released" %}Done{% else %}Pending{% endif %}
                  </span>
                </div>
              {% endfor %}

              {% if visit.stage == "lab" %}
                <form method="post" action="{% url 'clinical:release_lab_results' visit.queue_number %}" style="margin-top:14px">
                  {% csrf_token %}
                  <label>Enter results for each test</label>

                  {% for lt in lab_tests %}
                    <div class="lab-entry">
                      <span class="name">{{ lt.name }}</span>
                      <input type="text"
                             name="result_{{ lt.id }}"
                             placeholder="Enter result..."
                             value="{% if lt.result %}{{ lt.result.value }}{% endif %}"
                             autocomplete="off">
                    </div>
                  {% endfor %}

                  <button type="submit" class="btn-teal" style="margin-top:12px;width:100%">
                    Release results and send back to doctor
                  </button>
                </form>
              {% endif %}
            {% else %}
              <p class="sub" style="margin:0">No lab tests ordered.</p>
            {% endif %}
          </div>
        {% endif %}
'''

with open(path, "a", encoding="utf-8") as f:
    f.write(CHUNK_4)

print("Appended CHUNK_4 (", len(CHUNK_4), "chars )")