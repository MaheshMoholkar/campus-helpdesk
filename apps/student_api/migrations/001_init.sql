-- Mock student API tables (docs/spec.md section 11.6). Runs with search_path = student_api.
-- The chat API never reads this schema; it only calls the HTTP endpoints.

CREATE TABLE users (
    id            text PRIMARY KEY,           -- also the login name, e.g. S1001
    kind          text NOT NULL CHECK (kind IN ('student', 'staff')),
    name          text NOT NULL,
    college_code  text NOT NULL,
    program       text,
    year          int,
    section       text,
    password_hash text NOT NULL
);

CREATE TABLE fee_dues (
    student_id text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    term       text NOT NULL,
    amount_due numeric(10, 2) NOT NULL,
    due_date   date NOT NULL,
    status     text NOT NULL CHECK (status IN ('due', 'paid', 'overdue')),
    PRIMARY KEY (student_id, term)
);

CREATE TABLE attendance (
    student_id  text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    course_code text NOT NULL,
    attended    int  NOT NULL,
    total       int  NOT NULL,
    as_of       date NOT NULL,
    PRIMARY KEY (student_id, course_code)
);

CREATE TABLE timetable (
    college_code text NOT NULL,
    program      text NOT NULL,
    year         int  NOT NULL,
    section      text NOT NULL,
    day          text NOT NULL,
    slot         text NOT NULL,
    course_code  text NOT NULL,
    room         text NOT NULL,
    PRIMARY KEY (college_code, program, year, section, day, slot)
);

CREATE TABLE bonafide_requests (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id text NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    purpose    text NOT NULL,
    status     text NOT NULL DEFAULT 'submitted',
    created_at timestamptz NOT NULL DEFAULT now()
);
